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

    /**
     * Feign 请求头透传拦截器（登录态 + 链路 ID + Seata 全局事务 XID 的跨服务接力棒）
     * 大白话：没有它的话，order-service 调 show-service 时，show-service 会两眼一抹黑，
     * 既不知道当前是谁在下单，也不知道自己正处在哪个 Seata 分布式事务里！
     */
    @Bean
    public RequestInterceptor userAndTxRelayInterceptor() {
        return template -> {
            // 1. 透传用户身份与角色头，供下游微服务继续做权限/归属校验
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
            // 2. 透传全局链路追踪 ID，保证跨服务日志能用同一个 traceId 串起来
            String traceId = UserContext.getTraceId();
            if (traceId != null && !traceId.isBlank()) {
                template.header(UserContext.HEADER_TRACE_ID, traceId);
            }
            // 3. 关键一步：透传 Seata 全局事务 XID，让 show-service 的 SeataXidWebFilter 绑定进同一个全局事务分支
            String xid = UserContext.getSeataXid();
            if (xid != null && !xid.isBlank()) {
                template.header(UserContext.HEADER_SEATA_XID, xid);
            }
        };
    }

    /** 注册 ShowSeatClient 的降级兜底工厂 Bean，供 OpenFeign + Sentinel 熔断时调用 */
    @Bean
    public ShowSeatClientFallbackFactory showSeatClientFallbackFactory() {
        return new ShowSeatClientFallbackFactory();
    }

    /** 开启 Feign BASIC 级别日志（记录请求方法、URL、响应状态码和耗时，排障清晰又不刷屏） */
    @Bean
    public Logger.Level feignLoggerLevel() {
        return Logger.Level.BASIC;
    }
}
