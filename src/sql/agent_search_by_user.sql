-- PostgreSQL 查询用户定义的主 Agent 配置文件脚本
-- `agent_profiles` 里存的是配置，不是图 。Agent 图只在进程内存里，由 AgentLoader 首次请求时懒构建，没有落库。
SELECT
  key                         AS agent_id,
  value->>'name'              AS name,
  value->>'role_title'        AS role_title,
  value->>'enabled'           AS enabled,
  updated_at
FROM store
WHERE prefix = 'agent_profiles.console_user' -- 可以写业务使用的用户id
ORDER BY key;