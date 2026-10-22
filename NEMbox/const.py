# KenHuang: 使配置文件夹符合XDG标准
import os
import tempfile

if os.name == "nt":
    import getpass
    import hashlib


class Constant:
    if "XDG_CONFIG_HOME" in os.environ:
        conf_dir = os.path.join(os.environ["XDG_CONFIG_HOME"], "netease-musicbox")
    else:
        conf_dir = os.path.join(os.path.expanduser("~"), ".netease-musicbox")
    config_path = os.path.join(conf_dir, "config.json")
    if "XDG_CACHE_HOME" in os.environ:
        cacheDir = os.path.join(os.environ["XDG_CACHE_HOME"], "netease-musicbox")
        if not os.path.exists(cacheDir):
            os.mkdir(cacheDir)
        download_dir = os.path.join(cacheDir, "cached")
        cache_path = os.path.join(cacheDir, "nemcache")
    else:
        download_dir = os.path.join(conf_dir, "cached")
        cache_path = os.path.join(conf_dir, "nemcache")
    if "XDG_DATA_HOME" in os.environ:
        dataDir = os.path.join(os.environ["XDG_DATA_HOME"], "netease-musicbox")
        if not os.path.exists(dataDir):
            os.mkdir(dataDir)
        cookie_path = os.path.join(dataDir, "cookie.txt")
        log_path = os.path.join(dataDir, "musicbox.log")
        storage_path = os.path.join(dataDir, "database.json")
    else:
        cookie_path = os.path.join(conf_dir, "cookie.txt")
        log_path = os.path.join(conf_dir, "musicbox.log")
        storage_path = os.path.join(conf_dir, "database.json")

    # Runtime discovery and the single-owner lock. Keep POSIX's existing /tmp
    # fallback; Windows defaults to a user-writable local application directory.
    _runtime_base = os.environ.get("XDG_RUNTIME_DIR") or (
        os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
        if os.name == "nt"
        else "/tmp"  # noqa: S108
    )
    runtime_dir = os.path.join(_runtime_base, "netease-musicbox")
    _uid = (
        hashlib.sha256(
            (
                getpass.getuser() + "\0" + os.path.normcase(os.path.abspath(conf_dir))
            ).encode("utf-8")
        ).hexdigest()[:24]
        if os.name == "nt"
        else os.getuid()
    )
    socket_path = os.path.join(runtime_dir, f"musicboxd-{_uid}.sock")
    lock_path = os.path.join(runtime_dir, f"musicboxd-{_uid}.lock")
