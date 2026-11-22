package com.pulseticket.feign.config;

import com.pulseticket.common.context.UserContext;
import com.pulseticket.feign.fallback.ShowSeatClientFallbackFactory;
import feign.Logger;
import feign.RequestInterceptor;
import org.springframework.boot.autoconfigure.AutoConfiguration;
import org.springframework.context.annotation.Bean;

/**
 * OpenFeign 全局配置与登录态/Seata XID 透传拦截器
 * 面试核心考点（登录态透传第三棒）：
 * 微服务 A 调用微服务 B 时，Feign 会构造新的 HTTP 请求，
 * 必须通过 RequestInterceptor 从 ThreadLocal (UserContext) 取出 userId / username 写入新请求头！
 */
@AutoConfiguration
public class FeignRelayAutoConfig {

    @Bean
    public RequestInterceptor userAndTxRelayInterceptor() {
        return template -> {
            Long userId = UserContext.getUserId();
            if (userId != null) {
                template.header(UserContext.HEADER_USER_ID, String.valueOf(userId));
            }
            String username = UserContext.getUsername();
            if (username != null && !username.isBlank()) {
                template.header(UserContext.HEADER_USERNAME, username);
            }
            String role = UserContext.getRole();
            if (role != null && !role.isBlank()) {
                template.header(UserContext.HEADER_USER_ROLE, role);
            }
            String traceId = UserContext.getTraceId();
            if (traceId != null && !traceId.isBlank()) {
                template.header(UserContext.HEADER_TRACE_ID, traceId);
            }
            String xid = UserContext.getSeataXid();
            if (xid != null && !xid.isBlank()) {
                template.header(UserContext.HEADER_SEATA_XID, xid);
            }
        };
    }

    @Bean
    public ShowSeatClientFallbackFactory showSeatClientFallbackFactory() {
        return new ShowSeatClientFallbackFactory();
    }

    @Bean
    public Logger.Level feignLoggerLevel() {
        return Logger.Level.BASIC;
    }
}
