use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH, Duration, Instant};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};
use tauri_plugin_dialog::DialogExt;

/// 引擎运行超时（10 分钟后强杀进程）
const ENGINE_TIMEOUT: Duration = Duration::from_secs(10 * 60);

// ==================== 日志 ====================

fn log_dir() -> PathBuf {
    let p = dirs::config_dir().unwrap_or_else(|| PathBuf::from("."))
        .join("排课助手").join("logs");
    let _ = fs::create_dir_all(&p);
    p
}

fn log_file() -> PathBuf {
    let now = chrono::Local::now();
    log_dir().join(format!("{}.log", now.format("%Y-%m-%d")))
}

fn app_log(msg: &str) {
    let now = chrono::Local::now().format("%H:%M:%S%.3f");
    let line = format!("[{}] {}\n", now, msg);
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_file())
        .and_then(|mut f| {
            use std::io::Write;
            f.write_all(line.as_bytes())
        });
    println!("{}", line.trim());
}

/// 把引擎日志另存一份到 %APPDATA%\排课助手\logs\。
///
/// 临时工作目录在排课结束后会被整个删掉，engine.log 是**唯一**记录求解过程的文件，
/// 不留一份的话失败现场就彻底没了（此前排查"跑不出结果"只能靠推测，就是栽在这里）。
fn preserve_engine_log(out_dir: &Path, task_id: &str) {
    let src = out_dir.join("engine.log");
    if !src.exists() {
        return;
    }
    let dst = log_dir().join(format!("{}.engine.log", task_id));
    match fs::copy(&src, &dst) {
        Ok(_) => app_log(&format!("已保留引擎日志: {}", dst.display())),
        Err(e) => app_log(&format!("保留引擎日志失败: {}", e)),
    }
}

// ==================== 配置 ====================

#[derive(Serialize, Deserialize, Clone)]
struct Config {
    /// 默认输出目录（用户上次选过的）
    default_output_dir: Option<String>,
    /// 点窗口 X 时：true=最小化到托盘，false=直接退出
    close_to_tray: bool,
    /// 硬限制预设条件（教务老师可在设置里查看/修改）
    hard_limits: HardLimits,
    /// 窗口状态（大小和位置，启动时恢复）
    window_state: Option<WindowState>,
    /// 上次使用的输入文件路径
    last_input_file: Option<String>,
}

#[derive(Serialize, Deserialize, Clone)]
struct WindowState {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

#[derive(Serialize, Deserialize, Clone)]
struct HardLimits {
    /// 时间安排
    schedule: ScheduleRule,
    /// 固定课程（day: 1=周一 ... 7=周日；period: 1-8）
    fixed_classes: Vec<FixedClass>,
    /// 班级设置
    classes: ClassRule,
    /// 连堂规则
    consecutive: ConsecutiveRule,
    /// 求解器配置（传给引擎）
    #[serde(default)]
    solver: SolverConfig,
    /// 额外硬约束（教务老师手动输入，传给引擎）
    #[serde(default)]
    extra_constraints: String,
}

#[derive(Serialize, Deserialize, Clone)]
struct SolverConfig {
    /// 单次求解时限（秒）
    #[serde(default = "default_max_time")]
    max_time_seconds: i32,
    /// 每门课的候选教师数（1-5）
    #[serde(default = "default_candidate_k")]
    teacher_candidate_k: i32,
    /// 目标方案数
    #[serde(default = "default_num_plans")]
    num_plans: i32,
}

impl Default for SolverConfig {
    fn default() -> Self {
        Self {
            max_time_seconds: 60,
            teacher_candidate_k: 5,
            num_plans: 3,
        }
    }
}

fn default_max_time() -> i32 { 60 }
fn default_candidate_k() -> i32 { 5 }
fn default_num_plans() -> i32 { 3 }

#[derive(Serialize, Deserialize, Clone)]
struct ScheduleRule {
    periods_per_day: i32,
    period_minutes: i32,
    morning_start: String,
    morning_end: String,
    afternoon_start: String,
    afternoon_end: String,
    /// 大课间（上午第3节后）
    long_break_after_period: i32,
    long_break_minutes: i32,
    /// 眼保健操（下午第1节后，即第6节后）
    eye_break_after_period: i32,
    eye_break_minutes: i32,
    /// 默认课间时长
    default_break_minutes: i32,
}

#[derive(Serialize, Deserialize, Clone)]
struct FixedClass {
    day: i32,
    period: i32,
    subject: String,
    note: String,
}

#[derive(Serialize, Deserialize, Clone)]
struct ClassRule {
    total_classes: i32,
    arts_start: i32,
    arts_end: i32,
    science_start: i32,
    science_end: i32,
    gaokao_policy: String,
    /// 班主任必须在自己担任班主任的那个班上至少任课 1 节（设置页开关，默认关）。
    /// ⚠️ 必须 serde(default)：老用户的 config.json 里没有这个字段，
    /// 少了它整个配置会反序列化失败、退回默认值，把用户设置全冲掉。
    #[serde(default)]
    homeroom_must_teach_own: bool,
}

#[derive(Serialize, Deserialize, Clone)]
struct ConsecutiveRule {
    subjects: Vec<String>,
    math_day: i32,
    chinese_day: i32,
    english_day: i32,
    rule: String,
    /// 仅语数英允许连堂（其他学科连堂节数强制为0）
    #[serde(default = "default_true")]
    only_core_subjects: bool,
}

fn default_true() -> bool { true }

impl Default for Config {
    fn default() -> Self {
        Self {
            default_output_dir: None,
            close_to_tray: true,
            hard_limits: HardLimits {
                schedule: ScheduleRule {
                    periods_per_day: 8,
                    period_minutes: 40,
                    morning_start: "08:00".to_string(),
                    morning_end: "12:20".to_string(),
                    afternoon_start: "14:30".to_string(),
                    afternoon_end: "16:55".to_string(),
                    long_break_after_period: 3,
                    long_break_minutes: 30,
                    eye_break_after_period: 6,
                    eye_break_minutes: 15,
                    default_break_minutes: 10,
                },
                fixed_classes: vec![
                    FixedClass {
                        day: 1,
                        period: 1,
                        subject: "班会".to_string(),
                        note: "由班主任上课".to_string(),
                    },
                    FixedClass {
                        day: 1,
                        period: 8,
                        subject: "研究性学习".to_string(),
                        note: "".to_string(),
                    },
                    FixedClass {
                        day: 4,
                        period: 8,
                        subject: "校本课".to_string(),
                        note: "".to_string(),
                    },
                ],
                classes: ClassRule {
                    total_classes: 25,
                    arts_start: 1,
                    arts_end: 4,
                    science_start: 5,
                    science_end: 25,
                    gaokao_policy: "新高考3+1+2".to_string(),
                    homeroom_must_teach_own: false,
                },
                consecutive: ConsecutiveRule {
                    subjects: vec!["语文".to_string(), "数学".to_string(), "英语".to_string()],
                    math_day: 2,
                    chinese_day: 3,
                    english_day: 4,
                    rule: "老师一般带2个班，一个班上午1-2节，另一个班上午4-5节".to_string(),
                    only_core_subjects: true,
                },
                solver: SolverConfig {
                    max_time_seconds: 60,
                    teacher_candidate_k: 5,
                    num_plans: 3,
                },
                extra_constraints: "".to_string(),
            },
            window_state: None,
            last_input_file: None,
        }
    }
}

/// %APPDATA%\排课助手\
fn config_dir() -> PathBuf {
    let dir = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("排课助手");
    let _ = fs::create_dir_all(&dir);
    dir
}

fn config_path() -> PathBuf {
    config_dir().join("config.json")
}

fn temp_work_dir() -> PathBuf {
    let dir = config_dir().join("temp");
    let _ = fs::create_dir_all(&dir);
    dir
}

/// 启动时清理 7 天前的临时任务目录
fn cleanup_old_temp_dirs() {
    let temp = temp_work_dir();
    let now = SystemTime::now();
    let cutoff = now - Duration::from_secs(7 * 24 * 3600);
    if let Ok(entries) = fs::read_dir(&temp) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let stale = fs::metadata(&path)
                    .and_then(|m| m.modified())
                    .map(|t| t < cutoff)
                    .unwrap_or(false);
                if stale {
                    let _ = fs::remove_dir_all(&path);
                }
            }
        }
    }
}

/// 文件信息（前端校验和结果展示用）
#[derive(Serialize)]
struct FileInfo {
    exists: bool,
    size_bytes: u64,
    size_human: String,
    modified_ts: i64,
}

#[tauri::command]
fn get_file_info(path: String) -> FileInfo {
    let p = Path::new(&path);
    match fs::metadata(p) {
        Ok(meta) => {
            let size = meta.len();
            let size_f = size as f64;
            let human = if size < 1024 {
                format!("{} B", size)
            } else if size < 1024 * 1024 {
                format!("{:.1} KB", size_f / 1024.0)
            } else {
                format!("{:.1} MB", size_f / (1024.0 * 1024.0))
            };
            let modified_ts = meta.modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_secs() as i64)
                .unwrap_or(0);
            FileInfo { exists: true, size_bytes: size, size_human: human, modified_ts }
        }
        Err(_) => FileInfo { exists: false, size_bytes: 0, size_human: "".into(), modified_ts: 0 },
    }
}

#[derive(Serialize, Deserialize)]
struct ValidateResult {
    valid: bool,
    errors: Vec<String>,
    warnings: Vec<String>,
    stats: std::collections::HashMap<String, serde_json::Value>,
}

#[tauri::command]
fn validate_input(app: tauri::AppHandle, path: String) -> ValidateResult {
    let failed = |msg: String| ValidateResult {
        valid: false,
        errors: vec![msg],
        warnings: vec![],
        stats: std::collections::HashMap::new(),
    };

    // 以前这里写死「用系统 `python` 跑 manifest_dir/../engine/validate_input.py」：
    // 那个路径来自**编译期**的 CARGO_MANIFEST_DIR，装到用户机器上并不存在；
    // 而且用户机器也没有 Python。结果是预检在开发版能跑、装完就必然失败
    // （连带「用 AI 转」也拿不到姓名映射 —— 它依赖这里的 stats）。
    //
    // 现在改成调用引擎自带的 `--validate` 子命令：与排课共用同一套引擎定位逻辑，
    // 开发版 / 安装版走同一条路径；引擎 exe 里已经打包了 openpyxl，
    // 用户机器无需安装 Python。
    let (program, mut args) = match build_engine_command(&app) {
        Ok(v) => v,
        Err(e) => return failed(format!("无法定位输入校验程序: {}", e)),
    };
    args.push("--validate".to_string());
    args.push(path);

    // ⚠️ 保留 UTF-8 环境变量：开发版走的是 `python engine.py`，管道下 python 会
    // 退回系统 ANSI 代码页（cp936/GBK），而这里按 UTF-8 解码 → 中文变乱码。
    // 安装版是打包 exe，它自己在代码里改了流编码，不依赖这两个变量。
    let output = Command::new(&program)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .args(&args)
        .output();

    match output {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            match serde_json::from_str::<ValidateResult>(&stdout) {
                Ok(r) => r,
                Err(_) => failed(format!(
                    "校验程序输出解析失败: {}",
                    stdout.lines().last().unwrap_or("")
                )),
            }
        }
        Err(e) => failed(format!("无法运行校验程序: {}", e)),
    }
}

/// 把当前窗口的位置与大小写进配置。
///
/// 原先这段在前端做（`getCurrentWindow().outerSize()`）——那是 core 的 window API，
/// 而本项目**没有 capabilities 配置**，调用会被 ACL 静默拒绝：
/// `config.json` 里的 `window_state` 一直是 `null`，窗口记忆从来没生效过。
/// 改成在 Rust 侧监听事件 + 查询，不经过 ACL。
fn save_window_state_from(win: &tauri::WebviewWindow) {
    if win.is_minimized().unwrap_or(false) {
        return; // 最小化时尺寸会变成一个很小的值，存进去下次就真恢复成小窗口
    }
    let Ok(size) = win.outer_size() else { return };
    let Ok(pos) = win.outer_position() else { return };
    let scale = win.scale_factor().unwrap_or(1.0);
    let size = size.to_logical::<u32>(scale);
    let pos = pos.to_logical::<i32>(scale);
    // 窗口被隐藏/最小化时 Windows 会给一个很离谱的负坐标，一并挡掉
    if size.width < 400 || size.height < 320 || pos.x < -10000 || pos.y < -10000 {
        return;
    }
    let mut cfg = load_config();
    cfg.window_state = Some(WindowState {
        x: pos.x,
        y: pos.y,
        width: size.width,
        height: size.height,
    });
    let _ = save_config(&cfg);
}

/// 拖动窗口时 Resized/Moved 每帧都会触发，这里限制最小写入间隔，别把磁盘写爆。
fn should_save_window_state() -> bool {
    static LAST: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
    let slot = LAST.get_or_init(|| Mutex::new(None));
    let Ok(mut g) = slot.lock() else { return false };
    let now = Instant::now();
    if let Some(t) = *g {
        if now.duration_since(t) < Duration::from_millis(800) {
            return false;
        }
    }
    *g = Some(now);
    true
}

fn load_config() -> Config {
    let p = config_path();
    match fs::read_to_string(&p) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => Config::default(),
    }
}

fn save_config(c: &Config) -> Result<(), String> {
    let s = serde_json::to_string_pretty(c).map_err(|e| e.to_string())?;
    fs::write(config_path(), s).map_err(|e| e.to_string())
}

// ==================== 排课结果 ====================

#[derive(Serialize)]
struct PlanInfo {
    index: i32,
    name: String,
    score: f64,
    note: String,
}

#[derive(Serialize)]
struct SchedulerResult {
    code: i32,
    message: String,
    warnings: Vec<String>,
    plans: Vec<PlanInfo>,
    output_xlsx: Option<String>,
    output_pdf: Option<String>,
}

// ==================== 任务 ID ====================

fn generate_task_id() -> String {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    format!("task_{}_{}", now.as_secs(), now.subsec_nanos())
}

// ==================== 引擎定位 ====================

/// 找排课引擎：
/// 1) 发布模式：resource_dir/engine/engine.exe（sidecar）
/// 2) 开发模式：优先 src-tauri/resources/engine/engine.exe，
///    然后 CARGO_MANIFEST_DIR/../engine/engine.py，最后 fallback mock_engine.py
fn build_engine_command(app: &tauri::AppHandle) -> Result<(String, Vec<String>), String> {
    // 发布模式 sidecar
    if let Ok(resource_dir) = app.path().resource_dir() {
        let sidecar = resource_dir.join("engine").join("engine.exe");
        if sidecar.exists() {
            return Ok((sidecar.to_string_lossy().to_string(), vec![]));
        }
    }
    // 开发模式：优先 engine.exe（和发布模式一致）
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let dev_exe = Path::new(manifest_dir).join("resources/engine/engine.exe");
    if dev_exe.exists() {
        return Ok((dev_exe.to_string_lossy().to_string(), vec![]));
    }
    // 然后真引擎 engine.py
    let engine_script = Path::new(manifest_dir).join("../engine/engine.py");
    let mock_script = Path::new(manifest_dir).join("../engine/mock_engine.py");

    let script = if engine_script.exists() {
        engine_script
    } else if mock_script.exists() {
        mock_script
    } else {
        return Err("未找到排课引擎（engine.exe / engine.py / mock_engine.py）".to_string());
    };

    for py in ["python", "py"] {
        if Command::new(py).arg("--version").output().is_ok() {
            return Ok((
                py.to_string(),
                vec![script.to_string_lossy().to_string()],
            ));
        }
    }
    Err("未找到 Python，请先安装 Python 并加入 PATH".to_string())
}

// ==================== Tauri 命令 ====================

#[tauri::command]
fn get_config() -> Config {
    load_config()
}

#[tauri::command]
fn set_config(config: Config) -> Result<(), String> {
    save_config(&config)
}

#[tauri::command]
fn pick_input_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let p = app
        .dialog()
        .file()
        .add_filter("Excel 文件", &["xlsx"])
        .blocking_pick_file();
    Ok(p.and_then(|fp| fp.as_path().map(|pb| pb.to_string_lossy().to_string())))
}

#[tauri::command]
fn pick_output_dir(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let p = app.dialog().file().blocking_pick_folder();
    Ok(p.and_then(|fp| fp.as_path().map(|pb| pb.to_string_lossy().to_string())))
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    Command::new("explorer")
        .arg(&path)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("settings") {
        w.show().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn show_main(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        w.show().map_err(|e| e.to_string())?;
        w.unminimize().map_err(|e| e.to_string())?;
        w.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn show_tutorial(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
        std::thread::sleep(std::time::Duration::from_millis(200));
        let _ = w.eval(r#"
            (function() {
                var overlay = document.getElementById('tutorial-overlay');
                if (overlay) overlay.classList.remove('hidden');
            })();
        "#);
    }
    Ok(())
}

// 仅保留新格式模板：旧的 3-sheet 模板（教师名单/班级名单/课程信息）与
// 引擎已移除的旧格式同名易混，且它两种格式都不被引擎接受，故一并删除。
#[tauri::command]
fn download_template(app: tauri::AppHandle) -> Result<(), String> {
    let save_path = app
        .dialog()
        .file()
        .set_file_name("排课输入模板.xlsx")
        .add_filter("Excel 文件", &["xlsx"])
        .blocking_save_file()
        .ok_or_else(|| "已取消".to_string())?;

    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let template_path = resource_dir.join("resources").join("input_template.xlsx");
    let data = fs::read(&template_path).map_err(|e| format!("读取模板失败: {}", e))?;
    let save_path = save_path.as_path().ok_or("无效的保存路径")?;
    fs::write(save_path, data).map_err(|e| format!("保存模板失败: {}", e))?;
    Ok(())
}

// ⚠️ 必须带 (async)。
/// 排课进度事件（问题3 外壳侧，2026-10-01）。
///
/// 之前外壳是等引擎退出后才去读 stdout，排课的 3 分钟里前端完全不知道跑到哪一步；
/// 现在边跑边读，把「阶段N/5」「方案 i/N」解析成下面这个结构给前端显示。
#[derive(Clone, Serialize)]
struct ProgressPayload {
    stage: i32,
    percent: i32,
    message: String,
    elapsed_sec: f64,
}

/// 最新一条进度。前端用 `get_progress` **轮询**它。
///
/// ⚠️ 为什么不用 Tauri 事件（`app.emit` + 前端 `listen`）：
/// 本项目的 webview **没有配置 capabilities**（`src-tauri/capabilities/` 不存在），
/// Tauri 2 里事件 API 属于 core 插件、要 ACL 放行，没配就会被拒绝；
/// 而前端 `listen` 失败是**静默**的，表现就是"进度条一直停在初始文案"
/// （2026-10-01 用户实测踩到）。应用自己的命令不走 ACL，轮询这条路是稳的。
fn progress_slot() -> &'static Mutex<Option<ProgressPayload>> {
    static SLOT: OnceLock<Mutex<Option<ProgressPayload>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

fn set_progress(p: Option<ProgressPayload>) {
    if let Ok(mut slot) = progress_slot().lock() {
        *slot = p;
    }
}

/// 前端轮询用：拿最新的排课进度；没在排课就返回 null。
#[tauri::command]
fn get_progress() -> Option<ProgressPayload> {
    progress_slot().lock().ok().and_then(|s| s.clone())
}

// ==================== 取消排课 ====================
//
// 用户实测提的：排一次约 3 分钟，中途想放弃只能关窗口（而关闭默认收进托盘，
// 进程还占着）。这里给一条"能停下来"的路。
//
// 两个必须踩对的地方：
//   ① 引擎是 PyInstaller onefile。Windows 上它是「bootloader 父进程 + 真正跑
//      Python 的子进程」两个进程；只杀父进程会留下子进程继续算，所以必须
//      taskkill /T 杀整棵树。
//   ② 被强杀的进程没法执行自己的清理代码，%TEMP% 下的 _MEIxxxxxx 解包目录
//      会留下来（约 105 MB）。外壳负责兜底清理（见 cleanup_mei_leftovers）。

/// 当前正在运行的引擎进程 PID（供 `cancel_scheduler` 使用）。
fn running_pid_slot() -> &'static Mutex<Option<u32>> {
    static SLOT: OnceLock<Mutex<Option<u32>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

/// 本轮排课是否已被用户取消。run_scheduler 据此把结果报成"已取消"而不是"失败"。
fn cancelled_flag() -> &'static AtomicBool {
    static FLAG: AtomicBool = AtomicBool::new(false);
    &FLAG
}

/// 取消正在进行的排课。返回 false 表示当前没有在跑的任务。
#[tauri::command(async)]
fn cancel_scheduler() -> Result<bool, String> {
    let pid = match running_pid_slot().lock().ok().and_then(|g| *g) {
        Some(p) => p,
        None => return Ok(false),
    };
    cancelled_flag().store(true, Ordering::SeqCst);
    app_log(&format!("用户取消排课：终止引擎进程树 PID={}", pid));
    match Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .output()
    {
        Ok(o) => app_log(&format!("taskkill 已执行，退出码={:?}", o.status.code())),
        Err(e) => app_log(&format!("taskkill 调用失败（由轮询循环兜底 kill）: {}", e)),
    }
    Ok(true)
}

/// 清理 PyInstaller onefile 留下的解包目录（被强杀时它自己来不及清）。
///
/// 只删 %TEMP% 下**名字以 `_MEI` 开头、且修改时间不早于 `since`** 的目录：
/// 时间窗是为了不误伤其它正在运行的 PyInstaller 程序；再加一个数量上限，
/// 异常情况下宁可留一点也不乱删。
fn cleanup_mei_leftovers(since: SystemTime) {
    let Ok(entries) = fs::read_dir(std::env::temp_dir()) else {
        return;
    };
    let mut removed = 0usize;
    for e in entries.flatten() {
        if removed >= 5 {
            break;
        }
        if !e.file_name().to_string_lossy().starts_with("_MEI") {
            continue;
        }
        let path = e.path();
        if !path.is_dir() {
            continue;
        }
        let fresh = e
            .metadata()
            .and_then(|m| m.modified())
            .map(|t| t >= since)
            .unwrap_or(false);
        if fresh && fs::remove_dir_all(&path).is_ok() {
            removed += 1;
            app_log(&format!("已清理引擎解包残留: {}", path.display()));
        }
    }
}

/// 打开日志目录。失败时用户能自己去翻引擎日志（`<任务ID>.engine.log`），
/// 而不是只能看着一句"排课失败"干着急。
#[tauri::command]
fn open_logs_dir() -> Result<(), String> {
    Command::new("explorer")
        .arg(log_dir())
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 取字符串开头的连续数字（引擎日志里的「阶段3/5」「方案 2/3」都靠它）。
fn leading_int(s: &str) -> Option<i64> {
    let digits: String = s.trim_start().chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() {
        None
    } else {
        digits.parse().ok()
    }
}

/// 从 `[15:16:38 +  61.0s] ...` 里取出 61.0。
fn trailing_elapsed(line: &str) -> f64 {
    let Some(plus) = line.find('+') else { return 0.0 };
    let rest = &line[plus + 1..];
    let Some(s) = rest.find('s') else { return 0.0 };
    rest[..s].trim().parse().unwrap_or(0.0)
}

/// 去掉 `[HH:MM:SS + 12.3s] ` 前缀，给前端一句干净的话。
fn strip_log_prefix(line: &str) -> String {
    if line.starts_with('[') {
        if let Some(end) = line.find(']') {
            return line[end + 1..].trim().to_string();
        }
    }
    line.trim().to_string()
}

/// 把引擎的一行日志解析成进度；不是进度行就返回 None。
///
/// 认这些形态（都来自 engine/engine.py 与 solver/solve.py 的 elog）：
///   `阶段1/5: 加载输入文件...`                    → 10%
///   `  方案 1/3「均衡方案」求解中（本套预算 60 秒）...` → 30% 起，按套数递增到 85%
///   `  方案 1/3「均衡方案」完成（用时 60.1 秒）`       → 同上，按"已完成"算
///   `=== 排课完成 ===`                              → 100%
fn parse_engine_progress(raw: &str) -> Option<ProgressPayload> {
    let elapsed = trailing_elapsed(raw);
    let msg = strip_log_prefix(raw);

    // ---- 阶段行：`阶段3/5: 构建CP-SAT模型...`
    // 后 4 个阶段都很短，把 30%~85% 留给最长的那一段：阶段 4 逐套方案求解
    if let Some(pos) = msg.find("阶段") {
        if let Some(n) = leading_int(&msg[pos + "阶段".len()..]) {
            let (percent, text) = match n {
                1 => (8, "正在读取输入文件…"),
                2 => (16, "正在校验数据（周课时总量、连堂是否有足够教师）…"),
                3 => (24, "正在构建排课模型…"),
                4 => (30, "正在求解排课方案（这一步最花时间）…"),
                5 => (90, "正在导出 Excel / PDF…"),
                _ => return None,
            };
            return Some(ProgressPayload {
                stage: n as i32,
                percent,
                message: text.to_string(),
                elapsed_sec: elapsed,
            });
        }
    }

    // ---- 输入规模：`  教师数: 92, 班级数: 25, 课程数: 300`
    if msg.contains("教师数") && msg.contains("班级数") {
        let num = |key: &str| -> Option<i64> {
            let i = msg.find(key)?;
            leading_int(&msg[i + key.len()..])
        };
        if let (Some(t), Some(c)) = (num("教师数:"), num("班级数:")) {
            let k = num("课程数:").unwrap_or(0);
            return Some(ProgressPayload {
                stage: 1,
                percent: 14,
                message: format!("已读取 {c} 个班、{t} 位教师、{k} 条课程"),
                elapsed_sec: elapsed,
            });
        }
    }

    // ---- 导出阶段的两个细节
    if msg.starts_with("Excel导出完成") {
        return Some(ProgressPayload {
            stage: 5,
            percent: 94,
            message: "Excel 已生成，正在生成 PDF…".to_string(),
            elapsed_sec: elapsed,
        });
    }
    if msg.starts_with("PDF导出完成") {
        return Some(ProgressPayload {
            stage: 5,
            percent: 97,
            message: "PDF 已生成，马上就好".to_string(),
            elapsed_sec: elapsed,
        });
    }

    if msg.contains("排课完成") {
        return Some(ProgressPayload {
            stage: 5,
            percent: 100,
            message: "排课完成".to_string(),
            elapsed_sec: elapsed,
        });
    }

    // ---- 逐套方案：`  方案 2/3「主科优先方案」求解中（本套预算 60 秒）...`
    if let Some(pos) = msg.find("方案 ") {
        let rest = &msg[pos + "方案 ".len()..];
        let idx = leading_int(rest)?;
        let after = &rest[rest.find('/')? + 1..];
        let total = leading_int(after)?;
        if idx <= 0 || total <= 0 || idx > total {
            return None;
        }
        let finished = rest.contains("完成");
        // 30 + [0,55]，按"已完成套数"给进度
        let done = if finished { idx } else { idx - 1 };
        let percent = (30 + (done * 55 / total) as i32).min(85);
        let name = msg
            .find('「')
            .and_then(|a| msg.find('」').map(|b| (a, b)))
            .map(|(a, b)| msg[a + '「'.len_utf8()..b].to_string())
            .unwrap_or_default();
        let name_part = if name.is_empty() {
            String::new()
        } else {
            format!("「{}」", name)
        };
        let message = if finished {
            format!("第 {idx}/{total} 套方案{name_part}完成（用了约 {elapsed:.0} 秒）")
        } else {
            format!("第 {idx}/{total} 套方案{name_part}求解中…（每套最多 60 秒）")
        };
        return Some(ProgressPayload {
            stage: 4,
            percent,
            message,
            elapsed_sec: elapsed,
        });
    }

    None
}

// Tauri 官方文档：「不含 async 关键字的命令跑在主线程上，除非标了 #[tauri::command(async)]」。
// 本函数要阻塞数分钟轮询引擎退出（循环里只有 sleep，没有任何消息泵），
// 跑在主线程上会把 WebView 的消息循环占死 → Windows 判定窗口"未响应"。
// 加 (async) 后改由线程池执行，主线程保持可响应。
#[tauri::command(async)]
fn run_scheduler(
    app: tauri::AppHandle,
    input_path: String,
    requirements: String,
    output_dir: String,
) -> Result<SchedulerResult, String> {
    app_log(&format!("=== 开始排课 ==="));
    // 让前端立刻有东西可显示（用户 2026-10-01 反馈：等了几分钟一直停在初始文案）
    set_progress(Some(ProgressPayload {
        stage: 0,
        percent: 1,
        message: "正在准备输入文件…".to_string(),
        elapsed_sec: 0.0,
    }));
    app_log(&format!("输入文件: {} ({} bytes)", input_path, fs::metadata(&input_path).map(|m| m.len()).unwrap_or(0)));
    app_log(&format!("输出目录: {}", output_dir));

    // 校验输入文件
    let input_meta = fs::metadata(&input_path).map_err(|_| "输入文件不存在，请重新选择".to_string())?;
    if input_meta.len() == 0 {
        return Err("输入文件为空，请检查 Excel 文件".to_string());
    }
    if input_meta.len() < 200 {
        return Err("输入文件太小，可能不是有效的 Excel 文件".to_string());
    }

    // 校验输出目录
    let out = Path::new(&output_dir);
    if !out.exists() {
        fs::create_dir_all(out).map_err(|_| "输出目录不存在，且无法创建".to_string())?;
    }

    // 保存上次使用的输入文件
    {
        let mut cfg = load_config();
        cfg.last_input_file = Some(input_path.clone());
        let _ = save_config(&cfg);
    }

    let task_id = generate_task_id();
    let work_dir = temp_work_dir().join(&task_id);
    let in_dir = work_dir.join("input");
    let out_dir = work_dir.join("output");
    app_log(&format!("任务ID: {}, 工作目录: {}", task_id, work_dir.display()));
    fs::create_dir_all(&in_dir).map_err(|e| format!("创建临时目录失败: {}", e))?;
    fs::create_dir_all(&out_dir).map_err(|e| format!("创建临时目录失败: {}", e))?;

    // 复制输入文件
    let ext = Path::new(&input_path)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("xlsx");
    let dest_input = in_dir.join(format!("input.{}", ext));
    fs::copy(&input_path, &dest_input).map_err(|e| format!("复制输入文件失败: {}", e))?;
    app_log(&format!("输入文件已复制到: {}", dest_input.display()));

    // 写 requirements.txt（用户特殊要求 + 额外硬约束）
    let cfg = load_config();
    let extra = &cfg.hard_limits.extra_constraints;
    let full_requirements = if extra.trim().is_empty() {
        requirements
    } else {
        format!("{}\n\n=== 额外硬约束 ===\n{}", requirements, extra)
    };
    fs::write(in_dir.join("requirements.txt"), full_requirements)
        .map_err(|e| format!("写入要求文件失败: {}", e))?;

    // 写 hard_limits.json
    let cfg = load_config();
    app_log(&format!("solver配置: max_time={}s, candidate_k={}, num_plans={}",
        cfg.hard_limits.solver.max_time_seconds,
        cfg.hard_limits.solver.teacher_candidate_k,
        cfg.hard_limits.solver.num_plans));
    let hl_json = serde_json::to_string_pretty(&cfg.hard_limits)
        .map_err(|e| format!("序列化硬限制失败: {}", e))?;
    fs::write(in_dir.join("hard_limits.json"), hl_json)
        .map_err(|e| format!("写入硬限制文件失败: {}", e))?;
    app_log("hard_limits.json 已写入");

    // 启动引擎
    let (program, extra_args) = build_engine_command(&app).map_err(|e| {
        if e.contains("未找到排课引擎") {
            "排课引擎未安装，请联系技术人员".to_string()
        } else if e.contains("未找到 Python") {
            "未找到 Python 运行环境，请联系技术人员".to_string()
        } else {
            e
        }
    })?;
    app_log(&format!("引擎路径: {}", program));
    app_log(&format!("引擎参数: {:?} {:?} {:?} {:?}", extra_args, in_dir, out_dir, task_id));
    let mut cmd = Command::new(&program);
    cmd.args(&extra_args);
    // 同 validate_input：外壳没有控制台，引擎 stdout 会退回 ANSI 代码页，
    // 日志里的中文就成了乱码。这里强制 UTF-8。
    cmd.env("PYTHONIOENCODING", "utf-8");
    cmd.env("PYTHONUTF8", "1");
    cmd.arg(&in_dir);
    cmd.arg(&out_dir);
    cmd.arg(&task_id);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    // 本轮开始时清掉上一轮的取消标记；引擎起来后把 PID 登记出去，
    // 前端点「取消排课」时 cancel_scheduler 才找得到它。
    cancelled_flag().store(false, Ordering::SeqCst);
    let start_time = Instant::now();
    let started_sys = SystemTime::now();   // 清理 _MEI 残留时的时间基准
    let mut child = cmd.spawn().map_err(|e| format!("启动排课引擎失败: {}", e))?;
    let pid = child.id();
    if let Ok(mut g) = running_pid_slot().lock() {
        *g = Some(pid);
    }
    app_log(&format!("引擎已启动, PID={}", pid));

    // 问题3（2026-10-01）：以前是等子进程退出后才 read_to_end，排课的几分钟里
    // 前端完全不知道跑到哪一步。改成起线程边跑边读：
    //   ① 逐行解析「阶段N/5」「方案 i/N」，用 scheduler-progress 事件推给前端；
    //   ② 顺带消掉一个隐患 —— 输出量超过管道缓冲区（Windows 约 4–64KB）时，
    //      子进程会阻塞在写操作上，而外壳正在 try_wait 里干等，双方一起卡住。
    let stdout_buf = Arc::new(Mutex::new(Vec::<u8>::new()));
    let stderr_buf = Arc::new(Mutex::new(Vec::<u8>::new()));

    let stdout_handle = child.stdout.take().map(|pipe| {
        let buf = Arc::clone(&stdout_buf);
        std::thread::spawn(move || {
            for chunk in BufReader::new(pipe).split(b'\n') {
                let Ok(bytes) = chunk else { break };
                let mut line = bytes;
                line.push(b'\n');
                if let Some(p) = parse_engine_progress(&String::from_utf8_lossy(&line)) {
                    set_progress(Some(p));
                }
                if let Ok(mut b) = buf.lock() {
                    b.extend_from_slice(&line);
                }
            }
        })
    });

    let stderr_handle = child.stderr.take().map(|mut pipe| {
        let buf = Arc::clone(&stderr_buf);
        std::thread::spawn(move || {
            let mut v = Vec::new();
            let _ = pipe.read_to_end(&mut v);
            if let Ok(mut b) = buf.lock() {
                b.extend_from_slice(&v);
            }
        })
    });

    // 轮询等待引擎退出；超过 10 分钟强杀进程
    let deadline = Instant::now() + ENGINE_TIMEOUT;
    let mut timed_out = false;
    let mut last_log = Instant::now();
    let exit_status = loop {
        match child.try_wait().map_err(|e| e.to_string())? {
            Some(status) => break Some(status),
            None => {
                // 用户点了「取消排课」：cancel_scheduler 已经 taskkill 过整棵树，
                // 这里再补一刀（taskkill 失败时兜底），然后按"取消"收尾。
                if cancelled_flag().load(Ordering::SeqCst) {
                    app_log("检测到取消请求，终止引擎");
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                if Instant::now() >= deadline {
                    app_log("引擎超时（10分钟），强制终止");
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break None;
                }
                // 每30秒记录一次心跳
                if last_log.elapsed() >= Duration::from_secs(30) {
                    let elapsed = start_time.elapsed().as_secs();
                    app_log(&format!("引擎运行中... PID={}, 已运行{}秒", pid, elapsed));
                    last_log = Instant::now();
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
    };

    // 进程已退出，先把 PID 摘掉：此后前端再点「取消排课」会得到"没有正在进行的任务"。
    if let Ok(mut g) = running_pid_slot().lock() {
        *g = None;
    }
    let cancelled = cancelled_flag().load(Ordering::SeqCst);
    let elapsed = start_time.elapsed().as_secs_f64();
    app_log(&format!(
        "引擎退出, 耗时{:.1}秒, 超时={}, 已取消={}",
        elapsed, timed_out, cancelled
    ));
    // 引擎已退出，剩下来的都是外壳自己的收尾工作
    set_progress(Some(ProgressPayload {
        stage: 5,
        percent: if timed_out { 0 } else { 98 },
        message: "正在读取排课结果…".to_string(),
        elapsed_sec: elapsed,
    }));

    // 收尾：引擎已退出，两个读取线程马上就会读到 EOF，join 一下拿到完整输出
    if let Some(h) = stdout_handle {
        let _ = h.join();
    }
    if let Some(h) = stderr_handle {
        let _ = h.join();
    }
    let stdout = stdout_buf.lock().map(|b| b.clone()).unwrap_or_default();
    let stderr = stderr_buf.lock().map(|b| b.clone()).unwrap_or_default();

    if !stdout.is_empty() {
        app_log(&format!("=== 引擎stdout ===\n{}", String::from_utf8_lossy(&stdout)));
    }
    if !stderr.is_empty() {
        app_log(&format!("=== 引擎stderr ===\n{}", String::from_utf8_lossy(&stderr)));
    }

    if cancelled {
        // 用户主动取消：不算失败，也不必保留引擎日志（那是留给异常排查用的）。
        cleanup_mei_leftovers(started_sys);
        let _ = fs::remove_dir_all(&work_dir);
        return Ok(SchedulerResult {
            code: 2,
            message: "已取消排课。可以修改条件后重新开始。".to_string(),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        });
    }

    if timed_out {
        // 超时同样是强杀，也会留下 _MEI 解包目录
        cleanup_mei_leftovers(started_sys);
        preserve_engine_log(&out_dir, &task_id);
        let _ = fs::remove_dir_all(&work_dir);
        return Ok(SchedulerResult {
            code: 2,
            message: "排课引擎运行超时（超过 10 分钟），已自动终止。请减少班级或课程数量后重试".to_string(),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        });
    }

    // 解析 status.json
    let status_path = out_dir.join("status.json");
    app_log(&format!("status.json 存在: {}", status_path.exists()));
    let result = if status_path.exists() {
        let s = fs::read_to_string(&status_path).map_err(|e| format!("读取排课结果失败: {}", e))?;
        app_log(&format!("status.json 内容:\n{}", s));
        let raw: serde_json::Value =
            serde_json::from_str(&s).map_err(|_| "排课结果格式错误，请重试".to_string())?;
        let code = raw.get("code").and_then(|v| v.as_i64()).unwrap_or(2) as i32;
        let message = raw
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("排课完成")
            .to_string();
        app_log(&format!("结果: code={}, message={}", code, message));
        let warnings = raw
            .get("warnings")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect()
            })
            .unwrap_or_default();

        let plans: Vec<PlanInfo> = raw
            .get("plans")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|p| {
                        Some(PlanInfo {
                            index: p.get("index")?.as_i64()? as i32,
                            name: p.get("name")?.as_str()?.to_string(),
                            score: p.get("score")?.as_f64()?,
                            note: p.get("note")?.as_str()?.to_string(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();

        let mut output_xlsx = None;
        let mut output_pdf = None;
        if code != 2 {
            let user_out = Path::new(&output_dir);
            let _ = fs::create_dir_all(user_out);
            for (src_name, field) in [
                ("result.xlsx", &mut output_xlsx),
                ("result.pdf", &mut output_pdf),
            ] {
                let src = out_dir.join(src_name);
                if src.exists() {
                    let dst = user_out.join(src_name);
                    if fs::copy(&src, &dst).is_ok() {
                        *field = Some(dst.to_string_lossy().to_string());
                    }
                }
            }
        }
        SchedulerResult {
            code,
            message,
            warnings,
            plans,
            output_xlsx,
            output_pdf,
        }
    } else if !exit_status.map(|s| s.success()).unwrap_or(false) {
        let stderr_str = String::from_utf8_lossy(&stderr);
        let friendly = if stderr_str.contains("input.xlsx") || stderr_str.contains("No such file") {
            "输入文件格式不对，请检查 Excel 是否包含所需数据".to_string()
        } else if stderr_str.contains("MemoryError") || stderr_str.contains("Memory") {
            "排课引擎内存不足，请减少班级或课程数量后重试".to_string()
        } else {
            format!("排课引擎出错，请检查输入文件后重试（{}）", stderr_str.trim().chars().take(100).collect::<String>())
        };
        SchedulerResult {
            code: 2,
            message: friendly,
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        }
    } else {
        SchedulerResult {
            code: 2,
            message: "排课引擎未返回结果，请重试".to_string(),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        }
    };

    // code=0（完全成功无警告）不必留存；有警告或失败都留一份引擎日志，便于事后追溯。
    if result.code != 0 {
        preserve_engine_log(&out_dir, &task_id);
    }

    let _ = fs::remove_dir_all(&work_dir);

    Ok(result)
}

// ==================== 入口 ====================

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // 启动时清理 7 天前的临时任务目录
            cleanup_old_temp_dirs();

            // 恢复主窗口大小和位置
            if let Some(main) = app.get_webview_window("main") {
                if let Some(ws) = load_config().window_state {
                    let _ = main.set_size(tauri::LogicalSize::new(ws.width, ws.height));
                    let _ = main.set_position(tauri::LogicalPosition::new(ws.x, ws.y));
                }
            }

            // ---- 托盘菜单 ----
            let show_item = MenuItem::with_id(app, "show", "打开主面板", true, None::<&str>)?;
            let settings_item = MenuItem::with_id(app, "settings", "设置", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &settings_item, &quit_item])?;

            let _tray = TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("排课助手")
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.unminimize();
                            let _ = w.set_focus();
                        }
                    }
                    "settings" => {
                        if let Some(w) = app.get_webview_window("settings") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button,
                        button_state,
                        ..
                    } = event
                    {
                        if button == MouseButton::Left && button_state == MouseButtonState::Up {
                            let app = tray.app_handle();
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.unminimize();
                                let _ = w.set_focus();
                            }
                        }
                    }
                })
                .build(app)?;

            // ---- 主窗口：关闭拦截 + 尺寸/位置记忆 ----
            if let Some(main) = app.get_webview_window("main") {
                let win = main.clone();
                main.on_window_event(move |event| match event {
                    WindowEvent::CloseRequested { api, .. } => {
                        let cfg = load_config();
                        if cfg.close_to_tray {
                            api.prevent_close();
                            let _ = win.hide();
                        }
                    }
                    // 位置和尺寸都记：只拖动、或只拉边框，都要能记住。
                    WindowEvent::Resized(_) | WindowEvent::Moved(_) => {
                        if should_save_window_state() {
                            save_window_state_from(&win);
                        }
                    }
                    _ => {}
                });
            }

            // ---- 设置窗口：关闭即隐藏 ----
            if let Some(settings) = app.get_webview_window("settings") {
                let settings_for_event = settings.clone();
                settings.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = settings_for_event.hide();
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_config,
            set_config,
            pick_input_file,
            pick_output_dir,
            run_scheduler,
            open_path,
            open_settings,
            show_main,
            quit_app,
            show_tutorial,
            download_template,
            get_file_info,
            validate_input,
            get_progress,
            cancel_scheduler,
            open_logs_dir
        ])
        .run(tauri::generate_context!())
        .expect("排课助手启动失败");
}

// ==================== 单元测试 ====================
// 只测纯函数（进度解析），不依赖 Tauri 运行时：
//     cargo test --bin paike-assistant
#[cfg(test)]
mod tests {
    use super::*;

    /// 进度解析必须认准引擎真实输出的这几种行（格式见 engine/scheduler/solver/solve.py）。
    #[test]
    fn progress_parses_engine_log_lines() {
        // 阶段行
        let p = parse_engine_progress("[15:16:38 +   0.0s] 阶段1/5: 加载输入文件...").unwrap();
        assert_eq!((p.stage, p.percent), (1, 8));
        let p = parse_engine_progress("[15:16:38 +   0.8s] 阶段4/5: 求解排课方案...").unwrap();
        assert_eq!((p.stage, p.percent), (4, 30));
        let p = parse_engine_progress("[15:16:38 + 181.4s] 阶段5/5: 导出结果文件...").unwrap();
        assert_eq!((p.stage, p.percent), (5, 90));

        // 输入规模行（应能把班级/教师数说出来，用户才知道引擎真的读进去了）
        let p = parse_engine_progress("[t + 0.0s]   教师数: 92, 班级数: 25, 课程数: 300").unwrap();
        assert_eq!(p.percent, 14);
        assert!(p.message.contains("25 个班") && p.message.contains("92 位教师"),
                "实际文案: {}", p.message);

        // 导出阶段的两个细节
        let p = parse_engine_progress("[t + 181.5s]   Excel导出完成, 耗时1.2s").unwrap();
        assert_eq!(p.percent, 94);
        let p = parse_engine_progress("[t + 182.0s]   PDF导出完成, 耗时0.5s").unwrap();
        assert_eq!(p.percent, 97);

        // 逐方案行：求解中 / 完成
        let p = parse_engine_progress(
            "[15:16:38 +   0.8s]   方案 1/3「均衡方案」求解中（本套预算 60 秒）...").unwrap();
        assert_eq!((p.stage, p.percent), (4, 30));
        assert!(p.message.contains("1/3") && p.message.contains("均衡方案"));

        let p = parse_engine_progress(
            "[15:17:39 +  61.0s]   方案 1/3「均衡方案」完成（用时 60.1 秒）").unwrap();
        assert_eq!(p.percent, 30 + 55 / 3);
        assert!(p.message.contains("完成"));
        assert_eq!(p.elapsed_sec, 61.0);

        let p = parse_engine_progress(
            "[15:19:39 + 181.4s]   方案 3/3「自习后置方案」完成（用时 60.2 秒）").unwrap();
        assert_eq!(p.percent, 85);

        // 结束行
        let p = parse_engine_progress("[15:19:40 + 182.6s] === 排课完成 ===").unwrap();
        assert_eq!(p.percent, 100);
    }

    /// 普通日志行不能被误判成进度（否则进度条会乱跳）。
    #[test]
    fn progress_ignores_other_lines() {
        for line in [
            "[15:16:38 +   0.0s] === 引擎启动 ===",
            // 注意：「教师数: …」那行是**要**被识别成进度的，见上一条测试
            "[15:16:38 +   0.0s]   加载完成, 耗时0.0s",
            "[15:16:38 +   0.8s]   模型构建完成, 耗时0.8s",
            "[15:17:39 +  61.0s]   ...",
            "",
        ] {
            assert!(parse_engine_progress(line).is_none(), "不应识别为进度: {line}");
        }
    }

    /// 进度必须单调不减，否则进度条会往回退。
    #[test]
    fn progress_is_monotonic() {
        let lines = [
            "[t + 0.0s] 阶段1/5: 加载输入文件...",
            "[t + 0.1s] 阶段2/5: 数据校验...",
            "[t + 0.2s] 阶段3/5: 构建CP-SAT模型...",
            "[t + 0.3s] 阶段4/5: 求解排课方案...",
            "[t + 0.4s]   方案 1/3「均衡方案」求解中（本套预算 60 秒）...",
            "[t + 60.4s]   方案 1/3「均衡方案」完成（用时 60.1 秒）",
            "[t + 60.5s]   方案 2/3「主科优先方案」求解中（本套预算 60 秒）...",
            "[t + 121.5s]   方案 2/3「主科优先方案」完成（用时 60.1 秒）",
            "[t + 121.6s]   方案 3/3「自习后置方案」求解中（本套预算 60 秒）...",
            "[t + 181.6s]   方案 3/3「自习后置方案」完成（用时 60.2 秒）",
            "[t + 182.0s] 阶段5/5: 导出结果文件...",
            "[t + 182.6s] === 排课完成 ===",
        ];
        let mut last = -1;
        for line in lines {
            if let Some(p) = parse_engine_progress(line) {
                assert!((0..=100).contains(&p.percent), "百分比越界: {}", p.percent);
                assert!(p.percent >= last, "进度回退了: {last} -> {} ({line})", p.percent);
                last = p.percent;
            }
        }
        assert_eq!(last, 100);
    }
}
