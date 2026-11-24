package com.pulseticket.common.context;

/**
 * 基于 ThreadLocal 的全链路用户上下文
 * 面试核心考点：
 * 1. Gateway AuthGlobalFilter 解析 JWT 后通过请求头 X-User-Id / X-Username / X-User-Role 下发；
 * 2. 下游微服务 UserInfoInterceptor 在 preHandle 写入 ThreadLocal；
 * 3. 必须在 afterCompletion 的 finally 中调用 clear() 即 ThreadLocal.remove()，
 *    防止 Tomcat 线程池复用导致“内存泄漏”与“跨用户串号事故”！
 */
public final class UserContext {

    /** 网关/Feign 透传用的标准 HTTP 请求头名称常量 */
    public static final String HEADER_USER_ID = "X-User-Id";
    public static final String HEADER_USERNAME = "X-Username";
    public static final String HEADER_USER_ROLE = "X-User-Role";
    public static final String HEADER_TRACE_ID = "X-Trace-Id";
    /** Seata 分布式事务全局 XID 请求头名称 */
    public static final String HEADER_SEATA_XID = "TX_XID";

    /** 每个请求线程独享的存储槽位，互不干扰（底层绑定在当前 Thread 对象的 ThreadLocalMap 上） */
    private static final ThreadLocal<Long> USER_ID_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> USERNAME_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> USER_ROLE_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> TRACE_ID_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> SEATA_XID_HOLDER = new ThreadLocal<>();

    private UserContext() {}

    /** 设置当前线程的登录用户 ID */
    public static void setUserId(Long userId) {
        USER_ID_HOLDER.set(userId);
    }

    /** 获取当前线程的登录用户 ID（未登录时返回 null） */
    public static Long getUserId() {
        return USER_ID_HOLDER.get();
    }

    /** 设置当前线程的用户名 */
    public static void setUsername(String username) {
        USERNAME_HOLDER.set(username);
    }

    /** 获取当前线程的用户名 */
    public static String getUsername() {
        return USERNAME_HOLDER.get();
    }

    /** 设置当前线程的用户角色（如 USER / ADMIN） */
    public static void setRole(String role) {
        USER_ROLE_HOLDER.set(role);
    }

    /** 获取当前线程的用户角色，未设置时默认降级为普通用户 USER */
    public static String getRole() {
        String r = USER_ROLE_HOLDER.get();
        return (r != null && !r.isBlank()) ? r : "USER";
    }

    /** 快捷判断当前操作人是否为管理员（用于 RBAC 接口鉴权） */
    public static boolean isAdmin() {
        return "ADMIN".equalsIgnoreCase(getRole());
    }

    /** 设置当前请求的链路追踪 ID */
    public static void setTraceId(String traceId) {
        TRACE_ID_HOLDER.set(traceId);
    }

    /** 获取当前请求的链路追踪 ID */
    public static String getTraceId() {
        return TRACE_ID_HOLDER.get();
    }

    /** 记录当前线程关联的 Seata 全局事务 XID */
    public static void setSeataXid(String xid) {
        SEATA_XID_HOLDER.set(xid);
    }

    /** 获取当前线程关联的 Seata 全局事务 XID */
    public static String getSeataXid() {
        return SEATA_XID_HOLDER.get();
    }

    /**
     * 清空当前线程的所有上下文槽位！
     * 非常关键：Tomcat 的工作线程是复用的，如果不在这里 remove，下一个用户的请求复用这个线程时就会读到上一个用户的 ID！
     */
    public static void clear() {
        USER_ID_HOLDER.remove();
        USERNAME_HOLDER.remove();
        USER_ROLE_HOLDER.remove();
        TRACE_ID_HOLDER.remove();
        SEATA_XID_HOLDER.remove();
    }
}
