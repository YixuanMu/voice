"""Configure the Windows distribution using a key entered on this computer."""

import getpass
import re
import sys
import tempfile
import warnings
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


def configure(ini_path, env_path, key):
    if not re.fullmatch(r"TDX-[A-Za-z0-9_-]{8,200}", key):
        raise ValueError("Key 格式不正确，请复制从通达信商城创建的数据服务 Key。")
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
        atomic_write(env_path, source)
    except OSError:
        atomic_write(ini_path, original)
        raise


def main():
    sdk = find_spec("tdxaidata")
    if sdk is None or sdk.origin is None:
        raise RuntimeError("找不到通达信 SDK，请重新运行 setup-live.bat。")
    ini_path = Path(sdk.origin).parent / "lib" / "TdxAiData.ini"
    print("请粘贴你自己的通达信数据服务 Key，再按 Enter。输入不会显示。")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        key = getpass.getpass("Key: ").strip()
    configure(ini_path, ROOT / ".env", key)
    print("实盘配置已保存。正在启动，交易时段会连接通达信。")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("配置未完成。请关闭原启动窗口，并直接双击 setup-live.bat 重试。", file=sys.stderr)
        raise SystemExit(1) from None
