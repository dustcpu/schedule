use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// 引擎运行超时（接口协议 §2：超时后强杀进程）
const ENGINE_TIMEOUT: Duration = Duration::from_secs(10 * 60);
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WindowEvent};
use tauri_plugin_dialog::DialogExt;

// ==================== 配置 ====================

#[derive(Serialize, Deserialize, Clone)]
struct Config {
    /// 默认输出目录（用户上次选过的）
    default_output_dir: Option<String>,
    /// 点窗口 X 时：true=最小化到托盘，false=直接退出
    close_to_tray: bool,
    /// 硬限制预设条件（教务老师可在设置里查看/修改）
    hard_limits: HardLimits,
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
}

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
}

#[derive(Serialize, Deserialize, Clone)]
struct ConsecutiveRule {
    subjects: Vec<String>,
    math_day: i32,
    chinese_day: i32,
    english_day: i32,
    rule: String,
}

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
                },
                consecutive: ConsecutiveRule {
                    subjects: vec!["语文".to_string(), "数学".to_string(), "英语".to_string()],
                    math_day: 2,
                    chinese_day: 3,
                    english_day: 4,
                    rule: "老师一般带2个班，一个班上午1-2节，另一个班上午4-5节".to_string(),
                },
            },
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
/// 2) 开发模式：CARGO_MANIFEST_DIR/../engine/mock_engine.py，用 python 解释器跑
fn build_engine_command(app: &tauri::AppHandle) -> Result<(String, Vec<String>), String> {
    // 发布模式 sidecar
    if let Ok(resource_dir) = app.path().resource_dir() {
        let sidecar = resource_dir.join("engine").join("engine.exe");
        if sidecar.exists() {
            return Ok((sidecar.to_string_lossy().to_string(), vec![]));
        }
    }
    // 开发模式 mock
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let mock_script = Path::new(manifest_dir).join("../engine/mock_engine.py");
    if mock_script.exists() {
        for py in ["python", "py"] {
            if Command::new(py).arg("--version").output().is_ok() {
                return Ok((
                    py.to_string(),
                    vec![mock_script.to_string_lossy().to_string()],
                ));
            }
        }
        return Err("未找到 Python，请先安装 Python 并加入 PATH".to_string());
    }
    Err("未找到排课引擎（engine.exe 或 mock_engine.py）".to_string())
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
fn run_scheduler(
    app: tauri::AppHandle,
    input_path: String,
    requirements: String,
    output_dir: String,
) -> Result<SchedulerResult, String> {
    let task_id = generate_task_id();
    let work_dir = temp_work_dir().join(&task_id);
    let in_dir = work_dir.join("input");
    let out_dir = work_dir.join("output");
    fs::create_dir_all(&in_dir).map_err(|e| e.to_string())?;
    fs::create_dir_all(&out_dir).map_err(|e| e.to_string())?;

    // 复制输入文件为 input.xlsx（保留原扩展名，统一叫 input.<ext>）
    let ext = Path::new(&input_path)
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("xlsx");
    let dest_input = in_dir.join(format!("input.{}", ext));
    fs::copy(&input_path, &dest_input).map_err(|e| format!("复制输入文件失败: {}", e))?;

    // 写 requirements.txt
    fs::write(in_dir.join("requirements.txt"), requirements)
        .map_err(|e| format!("写入要求文件失败: {}", e))?;

    // 写 hard_limits.json（硬限制预设条件，从配置里带过来）
    let cfg = load_config();
    let hl_json = serde_json::to_string_pretty(&cfg.hard_limits)
        .map_err(|e| format!("序列化硬限制失败: {}", e))?;
    fs::write(in_dir.join("hard_limits.json"), hl_json)
        .map_err(|e| format!("写入硬限制文件失败: {}", e))?;

    // 启动引擎
    let (program, extra_args) = build_engine_command(&app)?;
    let mut cmd = Command::new(&program);
    cmd.args(&extra_args);
    cmd.arg(&in_dir);
    cmd.arg(&out_dir);
    cmd.arg(&task_id);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("启动引擎失败: {}", e))?;

    // 独立线程读 stdout / stderr：管道缓冲区写满会阻塞引擎进程，必须并行读取
    let stdout_handle = {
        let mut pipe = child.stdout.take();
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(p) = pipe.as_mut() {
                let _ = p.read_to_end(&mut buf);
            }
            buf
        })
    };
    let stderr_handle = {
        let mut pipe = child.stderr.take();
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(p) = pipe.as_mut() {
                let _ = p.read_to_end(&mut buf);
            }
            buf
        })
    };

    // 轮询等待引擎退出；超过 10 分钟强杀进程（接口协议 §2）
    let deadline = Instant::now() + ENGINE_TIMEOUT;
    let mut timed_out = false;
    let exit_status = loop {
        match child.try_wait().map_err(|e| e.to_string())? {
            Some(status) => break Some(status),
            None => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    timed_out = true;
                    break None;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
    };

    let stderr_bytes = stderr_handle.join().unwrap_or_default();
    let _ = stdout_handle.join();

    if timed_out {
        // 超时：清理临时目录并直接返回失败（接口协议 §2“超时即返回”）
        let _ = fs::remove_dir_all(&work_dir);
        return Ok(SchedulerResult {
            code: 2,
            message: "排课超时（超过 10 分钟），已强制终止引擎进程，请检查数据或放宽约束后重试".to_string(),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        });
    }
    let engine_ok = exit_status.map(|s| s.success()).unwrap_or(false);

    // 解析 status.json
    let status_path = out_dir.join("status.json");
    let result = if status_path.exists() {
        let s = fs::read_to_string(&status_path).map_err(|e| e.to_string())?;
        let raw: serde_json::Value =
            serde_json::from_str(&s).map_err(|e| format!("status.json 解析失败: {}", e))?;
        let code = raw.get("code").and_then(|v| v.as_i64()).unwrap_or(2) as i32;
        let message = raw
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("未知")
            .to_string();
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

        // 把结果文件复制到用户选的输出目录
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
    } else if !engine_ok {
        let stderr = String::from_utf8_lossy(&stderr_bytes);
        SchedulerResult {
            code: 2,
            message: format!("引擎异常退出: {}", stderr.trim()),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        }
    } else {
        SchedulerResult {
            code: 2,
            message: "引擎未生成 status.json".to_string(),
            warnings: vec![],
            plans: vec![],
            output_xlsx: None,
            output_pdf: None,
        }
    };

    // 清理临时工作目录
    let _ = fs::remove_dir_all(&work_dir);

    Ok(result)
}

// ==================== 入口 ====================

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
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

            // ---- 主窗口关闭拦截 ----
            if let Some(main) = app.get_webview_window("main") {
                let main_for_event = main.clone();
                main.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        let cfg = load_config();
                        if cfg.close_to_tray {
                            api.prevent_close();
                            let _ = main_for_event.hide();
                        }
                    }
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
            quit_app
        ])
        .run(tauri::generate_context!())
        .expect("排课助手启动失败");
}
