# Kubernetes 故障排查实战手册

## 前言

本手册旨在为运维工程师提供 Kubernetes 集群故障排查的标准操作流程。涵盖 Pod 生命周期异常、网络故障、存储挂载失败等高频问题的诊断与处理方法。

## 第一章 Pod 异常状态排查

### 1.1 CrashLoopBackOff 排查

当 Pod 处于 CrashLoopBackOff 状态时，说明容器反复启动后崩溃。

排查步骤：

1. 执行 `kubectl describe pod <pod-name>` 查看事件日志
2. 执行 `kubectl logs <pod-name> --previous` 查看上一次崩溃前的日志
3. 如果日志为空，检查容器的 `command` 和 `args` 是否正确
4. 如果是 OOMKilled，增大 `resources.limits.memory` 值
5. 如果是应用代码错误，修复后重新部署

### 1.2 ImagePullBackOff 排查

当镜像拉取失败时：

1. 确认镜像名称和标签是否正确
2. 检查镜像仓库的认证密钥（ImagePullSecret）是否配置
3. 如果是私有仓库，确认 `kubectl get secret` 中是否存在对应的 dockerconfigjson
4. 测试网络连通性：`curl -v https://registry.example.com/v2/`

### 1.3 Pending 状态排查

Pod 长时间 Pending：

1. 执行 `kubectl describe pod` 查看 Events 中的调度失败原因
2. 如果是资源不足（Insufficient cpu/memory），扩容节点或调整 requests
3. 如果是节点亲和性不匹配，检查 nodeSelector 和 tolerations 配置
4. 如果是 PVC 未绑定，跳转到存储章节排查

## 第二章 网络故障排查

### 2.1 Service 访问不通

当通过 ClusterIP 或 NodePort 无法访问服务时：

1. 确认 Service 的 selector 与 Pod 的 labels 完全匹配
2. 执行 `kubectl get endpoints <service-name>` 确认 endpoints 不为空
3. 在 Pod 内部执行 `curl <clusterIP>:<port>` 测试
4. 如果 endpoints 为空，检查 Pod 的 readinessProbe 是否通过
5. 如果跨命名空间访问，使用完整 DNS：`<svc>.<namespace>.svc.cluster.local`

### 2.2 DNS 解析失败

集群内 DNS 解析异常排查：

1. 检查 CoreDNS Pod 状态：`kubectl get pods -n kube-system -l k8s-app=kube-dns`
2. 在问题 Pod 中执行：`nslookup kubernetes.default`
3. 如果 CoreDNS 正常但解析失败，检查 Pod 的 `dnsPolicy` 配置
4. 如果 CoreDNS 日志报错，通常是上游 DNS 服务器不可达

## 第三章 存储故障排查

### 3.1 PVC 绑定失败

PersistentVolumeClaim 处于 Pending 状态：

1. 检查 StorageClass 是否存在：`kubectl get sc`
2. 如果使用静态 PV，确认 PV 的 capacity 和 accessModes 与 PVC 匹配
3. 如果使用动态 provisioning，检查 CSI 驱动是否正常运行
4. 查看 PVC 事件：`kubectl describe pvc <name>`

### 3.2 挂载超时

容器启动时挂载存储卷超时：

1. 检查节点上的 kubelet 日志
2. 对于 NFS：确认 NFS 服务器可达且共享路径存在
3. 对于云盘：确认云盘的可用区与节点一致
4. 增大 `spec.volumes[].timeoutSeconds` 参数

## 第四章 集群级别故障

### 4.1 etcd 集群不健康

etcd 是 Kubernetes 的核心数据存储：

1. 检查 etcd 成员列表：`etcdctl member list`
2. 检查集群健康状态：`etcdctl endpoint health --cluster`
3. 如果有成员 unreachable，检查该节点的网络和磁盘 I/O
4. 如果磁盘 I/O 过高，将 etcd 数据目录迁移到 SSD
5. 紧急情况下，执行 `etcdctl snapshot save` 备份数据后再进行修复

### 4.2 API Server 响应缓慢

当 kubectl 命令执行缓慢时：

1. 检查 API Server 的 CPU 和内存使用率
2. 如果大量 LIST 请求，启用 API Priority and Fairness 限流
3. 检查 etcd 的延迟指标：`etcdctl endpoint status`
4. 如果 webhook 响应慢，检查 ValidatingWebhookConfiguration 的 timeout 设置

## 致谢

感谢开源社区的所有贡献者。

## 参考文献

1. Kubernetes 官方文档 https://kubernetes.io/docs
2. etcd 运维手册
