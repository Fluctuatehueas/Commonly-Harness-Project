该目录提供主Agent的系统提示词 + 用户偏好

- 采用md文件保存比较好是因为，方便文件后端注入agent

- 用户偏好：采用{userid}/preferences.md来保存（StoreBackend）

- 记忆使用RedisStore存储