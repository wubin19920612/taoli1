codex
**结论：可部署＝否。发现 3 项 P1，无 P0。**

审查范围为 `6dcf2f2..ff6cda8`；全程未修改文件、未提交，结束时工作区仍干净。

1. **P1：8443 部署下，合法写请求被 CSRF 校验拒绝。**  
   位置：[routes_live_exec.py:188](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:188)、[frontend/nginx.conf:36](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/nginx.conf:36)。  
   外层 nginx 保留 `Host: 域名:8443`，内层却用 `$host` 转发，丢失端口；浏览器的 `Origin: https://域名:8443` 因而与后端计算的 HTTPS 默认端口 443 不一致。[nginx 官方说明](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)确认应使用 `$http_host` 保留原始 Host。离线复现：丢端口返回 **403**，保留端口返回 **200**。  
   **修复建议：**两层反代完整保留外部 Host/端口，补充与实际配置绑定的 8443 回归测试；不要通过忽略端口或信任任意转发头解决。

2. **P1：默认部署配置不会输出新增操作审计。**  
   位置：[routes_live_exec.py:27](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:27)、[routes_live_exec.py:334](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:334)。  
   审计使用普通 logger 的 `INFO`，没有配置级别和输出 handler。按 Dockerfile 的默认 Uvicorn 日志配置复现，实际为 `effective_level=WARNING、info_enabled=false`；锁定版本的[官方配置](https://raw.githubusercontent.com/Kludex/uvicorn/0.54.0/uvicorn/config.py)同样只配置 Uvicorn 自身日志。因此写入成功也没有预期的操作者审计；上游仅记录 `source=admin_api`，不能补足会话归属。现有测试通过 `caplog.at_level(INFO)` 掩盖了问题。  
   **修复建议：**配置独立审计 logger、INFO 级别和容器输出 handler，保留当前脱敏规则，并用生产日志配置验证成功与拒绝记录确实输出。

3. **P1：确认弹窗缺少服务端版本条件，会覆盖并发收紧的交易限制。**  
   位置：[LiveExecAdmin.tsx:84](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExecAdmin.tsx:84)、[liveExec.ts:132](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/api/liveExec.ts:132)；上游：[settings.py:97](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/settings.py:97)。  
   版本检查只比较浏览器缓存；请求仅发送修改值。已离线复现：弹窗显示 `order_usd_max：50 → 30`，另一会话在下一次轮询前改成 **5**，旧弹窗仍提交成功，实际执行 **5 → 30**。用户以为收紧限制，实际放宽了限制。  
   **修复建议：**请求携带预期版本，上游在更新事务内原子比较；冲突返回 409/412，刷新后重新确认。提交前单独 GET 仍有竞态。

其余检查中，未发现会话鉴权旁路、请求路径注入或管理员令牌泄露：密码与 2FA 登录链路正常；匿名、机器令牌、过期及撤销会话均不能写；288 组方法与路径变体没有产生未授权上游调用。跨源及伪造转发头请求被拒绝。字段、类型和 8192 字节限制有效，22 项参数边界与上游一致；首次点击仅打开确认框。

测试方面，后端定向 **185 项通过**，但未覆盖上述三种失败场景。既有前端测试确有覆盖范围收窄：[LiveExecPage.test.tsx:10](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/tests/LiveExecPage.test.tsx:10)屏蔽了管理组件，并将总请求数断言改为仅统计原业务请求；未发现新增跳过用例。新增管理测试不能替代反代、生产日志配置和轮询间隔内并发测试。

验证使用内存数据库、模拟上游及离线 DOM；生产锁定依赖和真实反代链路未实测。
tokens used
152,055
**结论：可部署＝否。发现 3 项 P1，无 P0。**

审查范围为 `6dcf2f2..ff6cda8`；全程未修改文件、未提交，结束时工作区仍干净。

1. **P1：8443 部署下，合法写请求被 CSRF 校验拒绝。**  
   位置：[routes_live_exec.py:188](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:188)、[frontend/nginx.conf:36](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/nginx.conf:36)。  
   外层 nginx 保留 `Host: 域名:8443`，内层却用 `$host` 转发，丢失端口；浏览器的 `Origin: https://域名:8443` 因而与后端计算的 HTTPS 默认端口 443 不一致。[nginx 官方说明](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)确认应使用 `$http_host` 保留原始 Host。离线复现：丢端口返回 **403**，保留端口返回 **200**。  
   **修复建议：**两层反代完整保留外部 Host/端口，补充与实际配置绑定的 8443 回归测试；不要通过忽略端口或信任任意转发头解决。

2. **P1：默认部署配置不会输出新增操作审计。**  
   位置：[routes_live_exec.py:27](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:27)、[routes_live_exec.py:334](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/backend/app/api/routes_live_exec.py:334)。  
   审计使用普通 logger 的 `INFO`，没有配置级别和输出 handler。按 Dockerfile 的默认 Uvicorn 日志配置复现，实际为 `effective_level=WARNING、info_enabled=false`；锁定版本的[官方配置](https://raw.githubusercontent.com/Kludex/uvicorn/0.54.0/uvicorn/config.py)同样只配置 Uvicorn 自身日志。因此写入成功也没有预期的操作者审计；上游仅记录 `source=admin_api`，不能补足会话归属。现有测试通过 `caplog.at_level(INFO)` 掩盖了问题。  
   **修复建议：**配置独立审计 logger、INFO 级别和容器输出 handler，保留当前脱敏规则，并用生产日志配置验证成功与拒绝记录确实输出。

3. **P1：确认弹窗缺少服务端版本条件，会覆盖并发收紧的交易限制。**  
   位置：[LiveExecAdmin.tsx:84](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/pages/liveExec/LiveExecAdmin.tsx:84)、[liveExec.ts:132](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/src/api/liveExec.ts:132)；上游：[settings.py:97](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/crossex-exec-r8/tools/crossex_exec/settings.py:97)。  
   版本检查只比较浏览器缓存；请求仅发送修改值。已离线复现：弹窗显示 `order_usd_max：50 → 30`，另一会话在下一次轮询前改成 **5**，旧弹窗仍提交成功，实际执行 **5 → 30**。用户以为收紧限制，实际放宽了限制。  
   **修复建议：**请求携带预期版本，上游在更新事务内原子比较；冲突返回 409/412，刷新后重新确认。提交前单独 GET 仍有竞态。

其余检查中，未发现会话鉴权旁路、请求路径注入或管理员令牌泄露：密码与 2FA 登录链路正常；匿名、机器令牌、过期及撤销会话均不能写；288 组方法与路径变体没有产生未授权上游调用。跨源及伪造转发头请求被拒绝。字段、类型和 8192 字节限制有效，22 项参数边界与上游一致；首次点击仅打开确认框。

测试方面，后端定向 **185 项通过**，但未覆盖上述三种失败场景。既有前端测试确有覆盖范围收窄：[LiveExecPage.test.tsx:10](/C:/Users/wubin/Desktop/code/codex/taoli1/.worktrees/live-exec-admin/frontend/tests/LiveExecPage.test.tsx:10)屏蔽了管理组件，并将总请求数断言改为仅统计原业务请求；未发现新增跳过用例。新增管理测试不能替代反代、生产日志配置和轮询间隔内并发测试。

验证使用内存数据库、模拟上游及离线 DOM；生产锁定依赖和真实反代链路未实测。
