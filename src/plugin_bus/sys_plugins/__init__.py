"""随包发布的系统内置插件源码目录。

每个子目录是一个内置插件：
``sys_plugins/{plugin_id}/manifest.json`` + 入口模块（默认 ``plugin.py``）。
内置插件通过 ``runtime.kind=inprocess`` 在宿主进程内运行，只读、不复制到用户目录。
"""
