from __future__ import annotations

from pathlib import Path
import pandas as pd


# Excel / WPS 等表格软件会把以这些字符开头的单元格当作公式执行；
# 用户上传的 SKU 名 / 备注流到结果 CSV 后，下载者打开就会被攻击。
# 出口前给这些值加单引号前缀（标准 OWASP "CSV Injection" 防御），文本本身保留。
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def read_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def write_csv(df: pd.DataFrame, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _escape_cell(value: object) -> object:
    if isinstance(value, str) and value and value[0] in _CSV_INJECTION_PREFIXES:
        return "'" + value
    return value


def to_csv_safe(df: pd.DataFrame, path: str | Path) -> None:
    """写 CSV 前对 object/string 列的危险前缀做转义，防止 Excel 公式注入。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df.to_csv(path, index=False)
        return
    safe = df.copy()
    for col in safe.columns:
        if safe[col].dtype == object or pd.api.types.is_string_dtype(safe[col]):
            safe[col] = safe[col].map(_escape_cell)
    safe.to_csv(path, index=False)
