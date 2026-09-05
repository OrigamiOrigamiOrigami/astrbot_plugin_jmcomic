import logging
import subprocess
import sys

logger = logging.getLogger("astrbot")

JMCOMIC_PACKAGE = "jmcomic"


def get_package_version(package_name: str):
    try:
        from importlib.metadata import version
        return version(package_name)
    except Exception:
        return None


def update_jmcomic_package():
    """升级 jmcomic 到 PyPI 最新版（pip install jmcomic -U）"""
    old_version = get_package_version(JMCOMIC_PACKAGE)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-U", JMCOMIC_PACKAGE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        new_version = get_package_version(JMCOMIC_PACKAGE)
        return True, old_version, new_version, None
    except subprocess.CalledProcessError as e:
        return False, old_version, get_package_version(JMCOMIC_PACKAGE), str(e)
    except Exception as e:
        return False, old_version, get_package_version(JMCOMIC_PACKAGE), str(e)


def reload_jmcomic_modules():
    """pip 升级后重新加载 jmcomic 模块"""
    global jmcomic, DirRule
    import importlib
    import jmcomic as jm_module
    importlib.reload(jm_module)
    from jmcomic.jm_option import DirRule as reloaded_dir_rule
    jmcomic = jm_module
    DirRule = reloaded_dir_rule
    return jmcomic


def probe_dependencies() -> bool:
    required = (("PIL", "pillow"), ("yaml", "pyyaml"), ("img2pdf", "img2pdf"), ("jmcomic", "jmcomic"))
    missing = []
    for mod, pkg in required:
        try:
            __import__("PIL" if mod == "PIL" else mod)
        except ImportError:
            missing.append(pkg)
            logger.error("缺少依赖: %s（请 pip install %s）", pkg, pkg)
    return not missing


if not probe_dependencies():
    logger.error("JMComic 插件依赖未满足，请安装 requirements.txt 后再加载")

# 导入依赖（如果安装成功）
try:
    from PIL import Image
    import yaml
    import img2pdf
    import jmcomic
    from jmcomic.jm_option import DirRule
except ImportError as e:
    logger.error(f"导入依赖失败: {e}")
    logger.error("请手动安装: pip install pillow pyyaml img2pdf jmcomic")
    Image = None
    yaml = None
    img2pdf = None
    jmcomic = None
    DirRule = None
