package com.pulseticket.common.interceptor;

import com.pulseticket.common.context.UserContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.servlet.HandlerInterceptor;

import java.util.UUID;

/**
 * 下游微服务统一登录态透传拦截器
 * 在 preHandle 提取网关或上游 Feign 透传的 X-User-Id / X-Username / X-User-Role，
 * 并在 afterCompletion 中强制调用 UserContext.clear() 清理 ThreadLocal！
 */
public class UserInfoInterceptor implements HandlerInterceptor {

    /**
     * 请求进入 Controller 之前执行：把 HTTP 请求头里的用户身份、角色和 traceId 塞进当前线程的 ThreadLocal
     * 这样后续 Service / Mapper 层随时调用 UserContext.getUserId() 就能拿到当前登录人，不用一层层传参。
     */
    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        // 1. 提取用户 ID（格式非法时静默忽略，当未登录处理）
        String userIdStr = request.getHeader(UserContext.HEADER_USER_ID);
        if (userIdStr != null && !userIdStr.isBlank()) {
            try {
                UserContext.setUserId(Long.parseLong(userIdStr.trim()));
            } catch (NumberFormatException ignored) {
            }
        }
        // 2. 提取用户名
        String username = request.getHeader(UserContext.HEADER_USERNAME);
        if (username != null && !username.isBlank()) {
            UserContext.setUsername(username.trim());
        }
        // 3. 提取角色并统一转大写（USER / ADMIN）
        String role = request.getHeader(UserContext.HEADER_USER_ROLE);
        if (role != null && !role.isBlank()) {
            UserContext.setRole(role.trim().toUpperCase());
        }
        // 4. 提取链路追踪 ID，如果上游（例如直连测试）没传，就在这里补生成一个
        String traceId = request.getHeader(UserContext.HEADER_TRACE_ID);
        if (traceId == null || traceId.isBlank()) {
            traceId = "tr-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        }
        UserContext.setTraceId(traceId);
        return true;
    }

    /**
     * 整个 HTTP 请求处理完毕（无论正常返回还是抛了异常）都会执行这里
     */
    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        // 面试核心考点：请求完成时无条件清理 ThreadLocal，杜绝线程池复用导致内存泄漏与用户串号
        UserContext.clear();
    }
}
