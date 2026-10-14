# 文件说明：PyInstaller 打包配置（onedir）。构建命令见 packaging/build.ps1。
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(PROJECT_ROOT / "data"), "data"),
    (str(PROJECT_ROOT / "apps" / "templates"), "apps/templates"),
    (str(PROJECT_ROOT / "apps" / "static"), "apps/static"),
]
binaries = []
hiddenimports = collect_submodules("uvicorn") + ["openpyxl"]

for package in ("ortools", "coptpy"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(PROJECT_ROOT / "apps" / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # ortools 的子模块会递归拖入 scipy -> networkx -> torch/onnxruntime/PIL（约 350MB），本项目一个都不用。
    excludes=[
        "tkinter", "matplotlib", "pytest", "IPython", "notebook", "PyQt5", "PySide2",
        "torch", "torchvision", "torchgen", "functorch", "onnxruntime",
        "scipy", "networkx", "sympy", "PIL", "pip",
        # pkg_resources 只被 packaging/pytz 的可选分支引用，但它的 runtime hook 会连累
        # jaraco -> backports（本环境没装），冻结后直接启动失败，这里整条链一起排除。
        "pkg_resources", "jaraco", "backports",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="仓储优化演示",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="仓储优化演示",
)
