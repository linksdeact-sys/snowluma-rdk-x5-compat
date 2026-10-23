# 修复日志：RDK X5 上 SnowLuma 已连接 QQ，但一直“等待登录 / 未就绪”

记录日期：2026-10-23（Asia/Tokyo）。

**结果：已恢复原生登录识别、OneBot 会话和真实聊天回复。** 本案例最终定位到 RDK 内核缺失的内存读取系统调用，以及手工加载原生模块时绕过兼容层的行为。

本文是一次真实设备的排查记录和兼容方案，供类似环境参考；不是 SnowLuma 官方修复公告。文中的账号、进程地址和部署路径已脱敏或替换为示例。随附代码是本次新增的兼容层与诊断脚本，不包含 QQ 或 SnowLuma 的原生二进制。

## 1. 实测环境

| 项目 | 本次验证环境 |
| --- | --- |
| 硬件 | RDK X5 |
| CPU 架构 | Linux AArch64 / ARM64 |
| 内核 | 6.1.83 |
| 关键内核配置 | `# CONFIG_CROSS_MEMORY_ATTACH is not set` |
| QQ | Linux ARM64 3.2.34-53644 |
| SnowLuma | v2.1.1，带一处独立的 TypeScript 账号身份检测补丁 |
| Node.js | 24.22.1 |
| 部署 | QQ 和 SnowLuma 位于同一个 Docker 容器 |
| 进程用户 | 本次 QQ 与 SnowLuma 均为容器 UID 0 |
| 消息链路 | QQ → SnowLuma → OneBot v11 WebSocket → 原有聊天后端 |

QQ 3.2.32-52194 也在加入本次内存兼容层之前做过对照，同样未就绪。**最终成功组合为 3.2.34-53644；不能据此宣称 3.2.32 已通过本修复验证。**

## 2. 故障现象

- 用户已经扫码并在手机确认，QQ 客户端进入真实聊天主界面。
- QQ 保存登录记录后，受控重启可以自动返回聊天界面。
- SnowLuma 能发现 QQ 进程，注入和管道连接成功，也能识别账号身份。
- 但原生管道始终返回未登录，WebUI 一直“等待登录”，OneBot 没有就绪账号。

脱敏后的状态摘要：

```json
{
  "injected": true,
  "connected": true,
  "loggedIn": false,
  "uin": "0",
  "status": "loaded",
  "localAccountDetected": true,
  "onebotAccountCount": 0
}
```

这份摘要将账号存在性转换成布尔值，不是原 API 的逐字输出。

日志可见 `login identity discovered ... awaiting readiness`。直接读取原生管道也得到 `loginState: status=0, flags=0, uin=0`，因此不是单纯的网页显示或 TypeScript 字段解析错误。

## 3. 排查过程与结论边界

| 阶段 | 操作 / 观察 | 得到的结论 |
| --- | --- | --- |
| 账号识别 | 修补 Linux QQ 主进程与数据库持有子进程之间的身份关联 | 能改善一个独立的漏识别场景，但身份已识别后仍然未就绪，不能当作完整修复 |
| 启动入口 | 对照官方同版本 deb，恢复 `./application.asar/app_launcher/index.js` | 纠正了一次部署入口错误，但故障仍能复现 |
| 原生文件 | 核对随附 `.node` 与 `.so` 和官方 v2.1.1 发行包一致 | 本次使用的文件不是混杂版本；不能把先前的旧文件问题继续当根因 |
| QQ 版本 | 对照 3.2.32-52194 与 3.2.34-53644 | 两个版本在修复前都失败，单独换版本没有解决 |
| 原生目标解析 | 部分函数地址非零，但 RTTI 类型信息为零、服务实例候选为空 | 缩小到原生解析阶段；当时还不足以认定是 QQ 二进制特征不兼容 |
| 内存读取自测 | 宿主机和容器中，读取自己的进程内存都返回 ENOSYS | 找到系统接口缺失的直接证据 |
| 内核配置 | `CONFIG_CROSS_MEMORY_ATTACH` 未启用 | 与缺失系统调用的结果一致 |
| 兼容层单独验证 | 用 `/proc/<pid>/mem` 读取，原生 resolver 恢复类型信息与服务实例候选 | 证明内存读取兼容层能解决前面的解析失败 |
| 实际模块接入 | 仅加 LD_PRELOAD 后仍失败；核对实际导入函数地址 | 手工 ELF 加载器把 hook 的读取函数直接绑定到了 libc，绕过兼容库 |
| 调整加载方式 | 使用正常动态链接器加载兼容库与随附 hook | 实际导入改为指向兼容库，随后原生确认登录、OneBot 建立会话 |
| 真实验证 | 收到用户测试消息，聊天后端连接，用户确认回复正常 | 本次消息链路修复成立 |

过程中曾把“原生组件未提供源码”视为修复的主要障碍。后续自测发现，只读系统接口兼容层即可解决关键故障，因此那个判断不够充分。

## 4. 根因：缺少 process_vm_readv

SnowLuma 随附的 Linux ARM64 `.node` 与 `.so` 都导入 `process_vm_readv`。在这台 RDK 上，自读测试结果为：

```text
process_vm_readv(self): return=-1, errno=38 (ENOSYS)
kernel config: # CONFIG_CROSS_MEMORY_ATTACH is not set
/proc/self/mem + pread: test data matches
```

关键点在于“读取自己的进程”也失败，而且宿主机也一样，不能只围绕容器用户、QQ 扫码或 OneBot Token 排查。单独一个 ENOSYS 在其他机器上还需要核对内核配置及运行环境，不能直接套用本案例的结论。

本目录的 `probe_kernel.py` 只测试自己分配的一段内存，不读取 QQ 账号、消息、登录凭证或聊天数据库：

```sh
python3 probe_kernel.py
```

## 5. 修复一：增加内存读取兼容层

新增 `process_vm_readv_compat.c`，行为如下：

1. 先调用原始 `process_vm_readv`。
2. 原始调用成功，或者失败原因不是 ENOSYS，就保留原结果。
3. 只有接口缺失时，打开 `/proc/<pid>/mem`，通过 `pread` 完成所需的 iovec 读取。
4. 保留目标进程访问检查，不额外放宽权限。

在 RDK 本机编译，不需要重新编译内核：

```sh
gcc -std=c11 -O2 -Wall -Wextra -Werror -fPIC -shared \
  -Wl,-z,relro,-z,now \
  -o libprocess-vm-readv-compat.so \
  process_vm_readv_compat.c -ldl -pthread
```

验证兼容层：

```sh
LD_PRELOAD="$PWD/libprocess-vm-readv-compat.so" python3 probe_kernel.py
LD_PRELOAD="$PWD/libprocess-vm-readv-compat.so" python3 test_memory_compat.py
```

这是一份面向本次 SnowLuma 有效 iovec 输入的兼容实现，不是经过完整 POSIX/Linux 行为一致性验证的通用 libc 替代品。它也没有实现 `process_vm_writev` 的回退。

## 6. 修复二：让实际 hook 使用兼容层

仅给 SnowLuma Node 加 `LD_PRELOAD` 并不足够：Node 一侧的 resolver 已能解析服务，但手工加载到 QQ 的 hook 仍指向 libc 的原始读取函数。

本次保留官方 hook 二进制，调整 QQ 的启动方式，让系统动态链接器加载它。以下路径均为**公开示例路径**，不是可直接复制到任意安装目录的一键脚本：

```sh
COMPAT=/opt/qq-compat/libprocess-vm-readv-compat.so
HOOK=/opt/snowluma/native/snowluma-linux-arm64.so

# 以下命令应在实际运行 QQ 的同一容器/系统中，以正确的 QQ 用户执行。
env LD_PRELOAD="$COMPAT:$HOOK" \
  SNOWLUMA_HOOK_STUB_START=1 \
  SNOWLUMA_HOOK_SERVICE_MODE=in-process \
  /opt/QQ/qq --no-sandbox
```

SnowLuma 的 Node 运行时单独加载兼容库：

```sh
env LD_PRELOAD="$COMPAT" /path/to/node /opt/snowluma/index.mjs
```

注意事项：

- 两条命令分别表示 QQ 和 SnowLuma 的启动设置；应改现有启动器，不能与仍在运行的 QQ 再启动一个实例。
- 保留原来的 HOME、QQ 资料目录、运行用户和设备相关配置。hook 来自匹配的官方发行包。
- 本次 hook 的构造函数会清除 QQ 子进程继承的 LD_PRELOAD，避免重复加载；需按版本核对，不能假定所有发行版相同。
- `SNOWLUMA_HOOK_STUB_START`、`SNOWLUMA_HOOK_SERVICE_MODE` 是本次 v2.1.1 验证过的内部开关，不承诺跨版本稳定。
- 未使用全系统 `/etc/ld.so.preload`，未修改 `loggedIn` 的真假判定，也未修改官方原生二进制内容。

## 7. 验证结果

### 兼容库测试

以下测试在 RDK 上通过：

- 自进程与父进程读取子进程内存。
- 本地与远端向量边界不同的 scatter/gather。
- 空向量、非法 flags、不存在的 PID、无效远端地址。
- 发生后续地址错误前的部分读取返回。
- 无权读取的 non-dumpable 子进程仍被拒绝。

权限拒绝测试在本次不具备 CAP_SYS_PTRACE 的测试容器中运行：

```sh
COMPAT_TEST_PERMISSION_GUARD=1 \
  LD_PRELOAD="$PWD/libprocess-vm-readv-compat.so" \
  python3 test_memory_compat.py
```

最后一项对测试身份有前提；有权读取目标进程的特权环境不应期待“权限拒绝”断言成立。

### 真实业务链路

```json
{
  "snowluma": {
    "injected": true,
    "connected": true,
    "loggedIn": true,
    "status": "online"
  },
  "onebot_get_login_info": {"status": "ok", "retcode": 0},
  "onebot_get_status": {
    "status": "ok",
    "retcode": 0,
    "data": {"online": true, "good": true}
  },
  "incoming_test_message_received": true,
  "chat_backend_connected": true,
  "user_confirmed_normal_reply": true
}
```

这是摘取关键字段后的脱敏摘要。测试没有通过强制修改在线字段获得成功；账号资料加载、真实消息接收和用户收到回复提供了进一步验证。

## 8. 适用范围和维护

- 已验证的是本表中的 RDK 内核、QQ 和 SnowLuma 组合；不能概括成所有 ARM64、所有 Linux 或所有“等待登录”故障。
- 相同界面症状还可能来自用户未登录、权限、原生版本不匹配等原因。先运行最小探针确认接口缺失。
- 这次解决的是原生会话未就绪，不代表已经验证 QQ 登录凭证长期不失效。
- 此次已验证文本消息链路；没有单独复测语音、图片、文件及所有 OneBot 动作。
- 普通容器重启可以保留已有启动修改。重建或升级镜像时，需要把兼容库和启动设置一起带入，不能只备份 QQ 资料目录。
- 应在重启 QQ 前保存当前启动配置和最新资料。回滚应恢复加载设置，避免用旧登录资料覆盖现有有效会话。

## 9. 在 GitHub 引用与反馈

本目录包含这份报告、独立诊断探针、兼容层 C 源码和对应测试。没有包含 QQ profile、聊天数据库、账号截图、Token、API Key、SSH 私钥、原生二进制或整份私人运行日志。

可将本文作为 Issue/Discussion 的参考材料，建议标题：

> [Linux ARM64 / RDK X5] CONFIG_CROSS_MEMORY_ATTACH disabled causes QQ hook to remain unready; process_vm_readv compatibility case study

若向 SnowLuma 提交 Issue，请遵照其 CONTRIBUTING.md，**通过 [GitHub 网页表单](https://github.com/SnowLuma/SnowLuma/issues/new/choose) 手动创建**。可以引用这份案例，附上自己设备的最小探针结果和实际版本信息。

上游项目：[SnowLuma](https://github.com/SnowLuma/SnowLuma)、[v2.1.1](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.1.1)。上游软件及原生组件继续受各自许可约束；本目录未打包或重新分发它们。
