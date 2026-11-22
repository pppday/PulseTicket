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

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        String userIdStr = request.getHeader(UserContext.HEADER_USER_ID);
        if (userIdStr != null && !userIdStr.isBlank()) {
            try {
                UserContext.setUserId(Long.parseLong(userIdStr.trim()));
            } catch (NumberFormatException ignored) {
            }
        }
        String username = request.getHeader(UserContext.HEADER_USERNAME);
        if (username != null && !username.isBlank()) {
            UserContext.setUsername(username.trim());
        }
        String role = request.getHeader(UserContext.HEADER_USER_ROLE);
        if (role != null && !role.isBlank()) {
            UserContext.setRole(role.trim().toUpperCase());
        }
        String traceId = request.getHeader(UserContext.HEADER_TRACE_ID);
        if (traceId == null || traceId.isBlank()) {
            traceId = "tr-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        }
        UserContext.setTraceId(traceId);
        return true;
    }

    @Override
    public void afterCompletion(HttpServletRequest request, HttpServletResponse response, Object handler, Exception ex) {
        // 面试核心考点：请求完成时无条件清理 ThreadLocal，杜绝线程池复用导致内存泄漏与用户串号
        UserContext.clear();
    }
}
