"""
状态码常量定义

基础状态码完全对应 Java 项目的 BaseResultDataConstant，保持完全一致。
业务状态码根据 kg-api-server 的业务场景自定义，格式：K + 5位数字（K 代表 Knowledge Graph）。
"""

# ==================== 基础状态码（与 Java 项目 BaseResultDataConstant 完全一致）====================

# 成功
SUCCESS = ("00000", "请求成功")

# 系统异常
ERROR = ("-1", "系统异常")
OPERATE_ERROR_EXCEPTION = ("E00001", "操作失败")

# 用户相关
USER_ACCOUNT_NOT_EXISTS = ("U0404", "用户名不存在")
USER_ACCOUNT_DISABLED = ("U0405", "用户名已被禁用")
USER_ACCOUNT_PWD_ERROR = ("U0406", "用户名登录密码不匹配")
USER_NOT_EXISTS_EXCEPTION = ("U10001", "用户不存在")
USER_LOGIN_PASSWORD_EXCEPTION = ("U10010", "密码验证失败")
USER_LOCKED_EXCEPTION = ("U10011", "用户已锁定")
USER_CAPTCHA_EXCEPTION = ("U10012", "验证码验证失败")

# TOKEN 相关
TOKEN_FAIL_OR_EXPIRE = ("A0230", "TOKEN校验失败或过期")
TOKEN_IS_NULL = ("A0231", "TOKEN为空")
TOKEN_SERVICE_FAIL = ("A0232", "TOKEN服务验证无响应数据")
TOKEN_ACCESS_PERMISSION_EXCEPTION = ("A0233", "TOKEN访问权限异常")
TOKEN_REQUEST_EXCEPTION = ("A0234", "TCAS服务请求异常")
TOKEN_USER_SYNC_EXCEPTION = ("A0235", "TCAS用户同步至本地异常")

# 权限相关
ACCESS_AUTHORITY_ERROR = ("A0301", "无访问权限")
OPERATE_AUTHORITY_ERROR = ("A0302", "无操作权限")

# 参数相关
PARAM_VERIFY_ERROR = ("A0400", "参数校验失败")
PARAM_NULL_ERROR = ("A0410", "必填参数为空")

# 数据相关
DATA_NOT_EXISTS = ("A0404", "数据不存在")
DATA_IS_EXISTS = ("A0406", "数据已存在")

# 业务相关
FOREIGN_KEY_EXCEPTION = ("B0342", "外键关联异常")
BUSINESS_CAS_ERROR = ("C0000", "TCAS接入异常")
DATA_SCOPE_NOT_EXISTS = ("DC0000", "数据范围不存在")

# 缓存相关
CACHE_PUT_EXCEPTION = ("CE10001", "存入缓存异常")
CACHE_GET_EXCEPTION = ("CE10002", "获取缓存异常")
CACHE_DEL_EXCEPTION = ("CE10003", "删除缓存异常")

# ==================== kg-api-server 业务状态码（自定义）====================

# 任务相关
KG_TASK_RUNNING = ("K10001", "当前有任务进行中")

# 图谱相关
KG_INVALID_GRAPH_NAME = ("K10002", "无效的图谱名称")
KG_NO_BASE_VERSION = ("K10003", "尚无 latest_ready_version，请先执行全量构建")
KG_NO_READY_VERSION = ("K10004", "当前没有可查询的已完成版本")

# 构建和更新相关
KG_BUILD_FAILED = ("K10005", "触发全量构建失败")
KG_UPDATE_FAILED = ("K10006", "触发增量更新失败")

