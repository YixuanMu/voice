"""Configure the Windows distribution using a key entered on this computer."""

import re
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_LINE = re.compile(rb"(?m)^[ \t]*token[ \t]*=[^\r\n]*")
SOURCE_LINE = re.compile(rb"(?m)^[ \t]*VOICE_SOURCE[ \t]*=[^\r\n]*(?:\r?\n|$)")


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temp = Path(output.name)
        try:
            output.write(content)
        except BaseException:
            output.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def normalize_key(text):
    key = text.strip(" \r\n\t\ufeff\u200b").strip('"\'').strip()
    if not re.fullmatch(r"(?i:tdx)-[A-Za-z0-9_-]{8,200}", key):
        raise ValueError("请在商城 Key 列表点击「复制」，再粘贴完整 Key。不要手输页面上的省略值。")
    return key


def verify_key():
    probe = (
        "from tdxaidata import tqs; "
        "data = tqs.get_market_snapshot_batch(stock_list=['600000.SH'], "
        "field_list=['LastClose'], return_df=False); "
        "print('VOICE_PROBE_OK' if isinstance(data, dict) and data.get('600000.SH', {}).get('LastClose') "
        "else 'VOICE_PROBE_EMPTY')"
    )
    try:
        response = subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=35)
    except subprocess.TimeoutExpired:
        raise ValueError("通达信连接超时。请检查网络后重试。") from None
    output = response.stdout + response.stderr
    if response.returncode == 0 and b"VOICE_PROBE_OK" in response.stdout.splitlines():
        return
    if b"TokenKey Insufficient Or Key Error" in output:
        raise ValueError("通达信拒绝了 Key。请检查 Key 是否启用、是否勾选通达信数据服务，以及账户积分。")
    raise ValueError("通达信未返回测试行情。请检查网络和数据权限后重试。")


def configure(ini_path, env_path, key, verify=None):
    key = normalize_key(key)
    original = ini_path.read_bytes()
    updated, count = TOKEN_LINE.subn(b"token=" + key.encode("ascii"), original)
    if count != 1:
        raise ValueError("SDK 配置里找不到唯一的 token 项。")
    env = env_path.read_bytes() if env_path.exists() else b""
    bom = b"\xef\xbb\xbf" if env.startswith(b"\xef\xbb\xbf") else b""
    env = SOURCE_LINE.sub(b"", env[len(bom):]).rstrip(b"\r\n")
    source = bom + (env + b"\n" if env else b"") + b"VOICE_SOURCE=tdx\n"
    atomic_write(ini_path, updated)
    try:
        if verify is not None:
            verify()
        atomic_write(env_path, source)
    except (OSError, ValueError):
        atomic_write(ini_path, original)
        raise


def main():
    sdk = find_spec("tdxaidata")
    if sdk is None or sdk.origin is None:
        raise RuntimeError("找不到通达信 SDK，请重新运行 setup-live.bat。")
    ini_path = Path(sdk.origin).parent / "lib" / "TdxAiData.ini"
    from tkinter import Tk, messagebox, simpledialog

    window = Tk()
    window.withdraw()
    try:
        while True:
            key = simpledialog.askstring("配置通达信实盘", "请粘贴商城复制的完整数据服务 Key（Ctrl+V）",
                                         show="*", parent=window)
            if key is None:
                raise SystemExit(2)
            try:
                configure(ini_path, ROOT / ".env", key, verify=verify_key)
            except ValueError as error:
                messagebox.showerror("通达信 Key 验证失败", str(error), parent=window)
                continue
            break
    finally:
        window.destroy()
    print("实盘配置已保存。正在启动，交易时段会连接通达信。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, EOFError, KeyboardInterrupt, ImportError):
        print("配置未完成。请关闭原启动窗口，并直接双击 setup-live.bat 重试。", file=sys.stderr)
        raise SystemExit(1) from None
