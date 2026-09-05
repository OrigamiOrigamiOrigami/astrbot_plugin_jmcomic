import logging
import os
import socket

logger = logging.getLogger("astrbot")


def get_proxy_host():
    """
    自动检测环境并返回合适的代理主机地址
    Docker环境使用 host.docker.internal，本地环境使用 127.0.0.1
    """
    try:
        if os.path.exists('/.dockerenv'):
            logger.info("检测到Docker环境，使用 host.docker.internal 作为代理主机")
            return 'host.docker.internal'

        try:
            socket.gethostbyname('host.docker.internal')
            logger.info("能够解析 host.docker.internal，判断为Docker环境")
            return 'host.docker.internal'
        except socket.gaierror:
            pass

        # 默认为本地环境
        logger.info("检测到本地环境，使用 127.0.0.1 作为代理主机")
        return '127.0.0.1'

    except Exception as e:
        logger.warning(f"环境检测失败，使用默认的 127.0.0.1: {str(e)}")
        return '127.0.0.1'


def get_default_proxy_config(port=7890):
    """获取默认的代理配置，自动适配环境"""
    proxy_host = get_proxy_host()
    return {
        'http': f'http://{proxy_host}:{port}',
        'https': f'http://{proxy_host}:{port}'
    }


# jmcomic 日志级别：off=关闭, summary=仅本子/章节/错误, full=全部
JMCOMIC_SUMMARY_LOG_TOPICS = [
    'album.before', 'album.after',
    'photo.before', 'photo.after',
    'image.failed', 'photo.failed',
    'req.fallback', 'req.error',
    'api.update_domain.error',
]


def apply_jmcomic_log_config(config_data: dict, log_level: str):
    """根据日志级别更新 option.yml 中的 jmcomic 日志配置"""
    plugins = config_data.setdefault('plugins', {})

    def remove_log_filter():
        for hook in list(plugins.keys()):
            if isinstance(plugins[hook], list):
                plugins[hook] = [
                    p for p in plugins[hook]
                    if not (isinstance(p, dict) and p.get('plugin') == 'log_topic_filter')
                ]

    if log_level == 'full':
        config_data['log'] = True
        remove_log_filter()
    elif log_level == 'summary':
        config_data['log'] = True
        remove_log_filter()
        after_init = plugins.setdefault('after_init', [])
        after_init.append({
            'plugin': 'log_topic_filter',
            'kwargs': {'whitelist': JMCOMIC_SUMMARY_LOG_TOPICS},
        })
    else:
        config_data['log'] = False
        remove_log_filter()

    return config_data
