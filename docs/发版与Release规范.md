# 发版与 Release 规范

> 起因：2026-10-01 整理仓库时发现，Release 页面上出现了一个**标题叫 `engine-v0.1.5`、
> 但挂在 `v0.1.5`（外壳版本）tag 上**的条目，与另一个正常的内核 Release
> （`engine-v0.1.5`）重复。原因是发版时没有统一命名口径。
> 本文件把口径固定下来，避免再出现。

## 1. 两个版本号，两条线

| 线 | tag 命名 | 内容 | 版本号写在 |
|---|---|---|---|
| **外壳 / 安装包** | `vX.Y.Z` | Tauri 桌面程序 + 安装包 | `src-tauri/tauri.conf.json`、`Cargo.toml`、`Cargo.lock`、`README.md`、`ui/settings.html` |
| **内核 / 引擎** | `engine-vX.Y.Z` | `engine.exe`（PyInstaller 单文件） | `engine/scheduler/__init__.py` |

两条线**版本号保持一致**（发版时一起改），但 **tag 名不能混用**——
不要出现「标题写 engine-vX.Y.Z、tag 却是 vX.Y.Z」这种情况。

## 2. 发版时必改的 7 处版本号

```bash
# 挨个改完再提交（漏一处就会出现"版本号对不上"）
src-tauri/tauri.conf.json     # version + longDescription
src-tauri/Cargo.toml          # version + description
src-tauri/Cargo.lock          # 本包 version（⚠️ 最容易漏）
README.md                     # 头部
ui/settings.html              # 「关于」里显示的版本号（历史上漏改过，长期停在 v0.1.3）
engine/scheduler/__init__.py  # 引擎自报版本
CHANGELOG.md                  # [未发布] 区转成新版本小节 + 那张版本号表
```

内核版本号改完**必须重新打包**，否则 `engine.exe` 依旧自报旧版本：

```bash
cd engine && python build_exe.py          # 产物 engine/dist/engine.exe
# 然后复制到前端实际会读的位置（否则前端行为毫无变化）：
cp engine/dist/engine.exe src-tauri/resources/engine/engine.exe
```

## 3. Release 命名与附件

**标题**（`name` 字段）统一写成：

```
vX.Y.Z —— 一句话主题           例：v0.1.7 —— 排课进度条 + 问题清单修复
engine-vX.Y.Z —— 引擎 X.Y.Z     例：engine-v0.1.7 —— 引擎 0.1.7
```

**tag 选择**：外壳 Release 选 `vX.Y.Z`；内核 Release 选 `engine-vX.Y.Z`。

**附件清单**：

| Release | 必备附件 | 说明 |
|---|---|---|
| `vX.Y.Z`（外壳） | `排课助手_X.Y.Z_x64-setup.exe` | 已内置内核，用户装上即用 |
| `engine-vX.Y.Z`（内核） | `engine.exe`（改名 `engine-vX.Y.Z.exe`） | 给需要替换 sidecar 的开发同学 |
| 两者都可附 | `release-notes-vX.Y.Z.md` | 说明正文，格式参考 `发布包/release-notes-v0.1.7.md` |

正文至少写：**本版修了什么（用户能感知的）** / **验证方式** / **已知未修项** / **回滚办法**。

## 4. 打 tag 的时机（⚠️ 顺序不能错）

```
1. 改完 7 处版本号 → 更新 CHANGELOG
2. 重新打包内核 exe + 安装包（安装包会内置内核，所以内核先打）
3. commit + push
4. git tag -a vX.Y.Z -m "..."     ← 在最终提交上打 tag
5. git push origin vX.Y.Z
6. 在 GitHub 上建 Release（用上一步的 tag），上传附件
```

**一旦 Release 发布，那个 tag 就不能再动了。** 移动一个已发布 Release 所依赖的 tag，
会让 Release 变成"找不到 tag"的孤儿条目。如果 Release 发出后又发现要补东西，
**升版本号走下一个版本**，不要回头改已发布的 tag。

## 5. git 提交约定

- 提交信息用中文 `type: 描述`（`feat` / `fix` / `docs` / `chore` / `release`）
- **禁止添加 AI 署名**（不要 `Co-Authored-By: Claude`、不要 `🤖 Generated with`）
- 推送前先在 `CHANGELOG.md` 的 `[未发布]` 区记录本次改动
- 推送前扫描：不要把 `*.xlsx` / `*.pdf` / `*.exe` 带进提交
  （`engine/test_input_*` 与 `src-tauri/resources/input_template.xlsx` 是**故意入库的合成样例**，属例外）

## 6. 不要入库的东西

`.gitignore` 已覆盖：`target/`、`engine/dist/`、`engine/build/`、`src-tauri/resources/engine/`、
`__pycache__/`。另外注意：

- **内核 `engine.exe`（105 MB）不入库**，走 Release 附件分发
- **安装包不入库**，放本地 `发布包/`
- **真实学校数据（教师/学生姓名、真实课表）绝对不入库**，仓库是公开的。
  测试请用 `engine/test_input_25_new/` 这份合成数据（占位命名 `T001` / `语文老师001`）
