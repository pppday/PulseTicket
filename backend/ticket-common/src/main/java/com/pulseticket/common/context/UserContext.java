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

    public static final String HEADER_USER_ID = "X-User-Id";
    public static final String HEADER_USERNAME = "X-Username";
    public static final String HEADER_USER_ROLE = "X-User-Role";
    public static final String HEADER_TRACE_ID = "X-Trace-Id";
    public static final String HEADER_SEATA_XID = "TX_XID";

    private static final ThreadLocal<Long> USER_ID_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> USERNAME_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> USER_ROLE_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> TRACE_ID_HOLDER = new ThreadLocal<>();
    private static final ThreadLocal<String> SEATA_XID_HOLDER = new ThreadLocal<>();

    private UserContext() {}

    public static void setUserId(Long userId) {
        USER_ID_HOLDER.set(userId);
    }

    public static Long getUserId() {
        return USER_ID_HOLDER.get();
    }

    public static void setUsername(String username) {
        USERNAME_HOLDER.set(username);
    }

    public static String getUsername() {
        return USERNAME_HOLDER.get();
    }

    public static void setRole(String role) {
        USER_ROLE_HOLDER.set(role);
    }

    public static String getRole() {
        String r = USER_ROLE_HOLDER.get();
        return (r != null && !r.isBlank()) ? r : "USER";
    }

    public static boolean isAdmin() {
        return "ADMIN".equalsIgnoreCase(getRole());
    }

    public static void setTraceId(String traceId) {
        TRACE_ID_HOLDER.set(traceId);
    }

    public static String getTraceId() {
        return TRACE_ID_HOLDER.get();
    }

    public static void setSeataXid(String xid) {
        SEATA_XID_HOLDER.set(xid);
    }

    public static String getSeataXid() {
        return SEATA_XID_HOLDER.get();
    }

    public static void clear() {
        USER_ID_HOLDER.remove();
        USERNAME_HOLDER.remove();
        USER_ROLE_HOLDER.remove();
        TRACE_ID_HOLDER.remove();
        SEATA_XID_HOLDER.remove();
    }
}
