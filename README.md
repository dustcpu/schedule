# 排课助手

> 版本：v0.1.0
> 作者：Dust.
> 技术栈：Tauri 2（Rust + 原生 HTML/CSS/JS）+ Python 排课引擎

一个 Win11 桌面端排课工具外壳。用户上传 Excel 数据、填写特殊要求，
外壳调用排课引擎，输出 Excel + PDF 课表。全程离线运行，不联网、不上传数据。

---

## 快速开始

详细编译步骤见 [安装说明.md](./安装说明.md)。

**开发模式**：
```bash
cd src-tauri
cargo run
```

**打包发布**：
```bash
cd src-tauri
cargo tauri build
```

---

## 目录结构

```
排课助手-github/
├── README.md              # 本文件
├── 安装说明.md            # 团队编译/环境准备
├── 接口协议.md            # 外壳 ↔ 引擎的调用约定（算法同学必读）
├── 启动-开发.bat          # Windows 一键启动开发模式
├── .gitignore
├── src-tauri/             # Rust 外壳
│   ├── Cargo.toml
│   ├── build.rs
│   ├── tauri.conf.json
│   ├── src/main.rs        # 托盘、配置、sidecar 调用
│   └── icons/icon.ico
├── ui/                    # 前端（原生 HTML/CSS/JS，无框架）
│   ├── index.html         # 主面板
│   ├── settings.html      # 设置页
│   └── style.css
└── engine/                # 算法引擎
    └── mock_engine.py     # 开发占位引擎（真正算法同学替换成 engine.exe）
```

---

## 工作原理

```
用户在界面上选 input.xlsx + 填特殊要求 + 选输出目录
                    ↓
排课助手.exe（外壳）
                    ↓ 调用 engine.exe <输入目录> <输出目录> <任务ID>
              算法引擎（Python）
                    ↓ 输出
result.xlsx（多 sheet：总览 + 方案1/2/3...）
result.pdf（多页：总览 + 每个方案一页课表）
status.json（含 plans 数组，3-8 套多解）
```

详细接口约定见 [接口协议.md](./接口协议.md)。

---

## 配置与数据位置

- 配置：`%APPDATA%\排课助手\config.json`
- 临时工作目录：`%APPDATA%\排课助手\temp\`
- 用户的输入/输出文件：完全由用户在界面上自选路径，软件不私自存副本

---

## 隐私说明

- 完全离线运行，不发起任何网络请求
- 不上传任何文件、不上传用户填写的内容
- 不注册全局键盘钩子
- 不读取与排课无关的系统文件
- 所有数据只在本地临时目录处理，用完即删

---

## 分支 / 协作说明

- `main`：稳定版
- 算法引擎同学：按 `接口协议.md` 实现，产出 `engine.exe`，放到 `src-tauri/resources/engine/`
- UI 同学：改 `ui/` 目录的 HTML/CSS，或 `src-tauri/src/main.rs`
