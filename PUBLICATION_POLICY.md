# 公开发布策略

本仓库采用“公开文件白名单 + 本地 Git hooks”。目标是让内部数据默认留在
本地，新增公开内容必须显式进入白名单。

## 本地数据放哪里

- 首选：放在仓库外。
- 必须放在仓库内时：统一放入 `.private/`。
- `.local/`、`local-data/`、`sessions/`、`screenshots/`、环境变量文件、
  日志、CSV 和数据库文件也默认忽略。
- 公开样例只能使用合成数据，并声明
  `meta.provenance: "synthetic"`。

不要将真实截图、会话记录、账号信息、浏览器数据、个人路径或密钥复制到
公开目录。

## 启用本地保护

每个新 clone 只需运行一次：

```bash
git config core.hooksPath .githooks
git config --get core.hooksPath
```

提交前会检查完整暂存区并运行测试；推送前会检查待发布分支和标签的全部
可达历史。检查范围包括：

- 路径是否在 `.public-allowlist`；
- 私有目录、未批准文件类型和非合成样例；
- 常见密钥、私钥、个人邮箱和机器本地路径。

可随时手动运行：

```bash
python3 scripts/public_check.py --working-tree
python3 scripts/public_check.py --index
python3 scripts/public_check.py --history
```

`--no-verify` 会绕过本地保护，不属于本项目的合规发布流程。

## 发布边界

GitHub Actions 暂缓启用，因此当前保护依赖每位贡献者启用 hooks。修改白名单
时必须单独审查新增路径和文件类型。

本仓库的公开历史必须持续通过历史检查。检查失败时不得推送或切换为
Public；历史重写和强制推送必须单独审核、授权和验证。
