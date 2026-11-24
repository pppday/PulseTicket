package com.pulseticket.gateway.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.pulseticket.common.context.UserContext;
import com.pulseticket.common.result.Result;
import com.pulseticket.common.security.JwtUtils;
import io.jsonwebtoken.Claims;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.http.server.reactive.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.util.AntPathMatcher;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;

/**
 * 网关全局登录态校验与透传过滤器 (面试第一高频必考题：登录态透传第一棒)
 * 核心职责：
 * 1. 拦截一切外部伪造的 /internal/** 内部接口调用；
 * 2. 放行登录注册、公开演出查询与微信扫码验票白名单；
 * 3. 统一校验 Bearer JWT 签名与过期时间；
 * 4. 【防伪造关键一步】：先 remove 掉外部可能伪造的 X-User-Id / X-Username / X-User-Role 请求头，
 *    再将验签解析出的真实 userId / username / role 注入请求头转发给下游微服务！
 */
@Component
public class AuthGlobalFilter implements GlobalFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AuthGlobalFilter.class);
    private final AntPathMatcher pathMatcher = new AntPathMatcher();
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Value("${pulseticket.jwt.secret:PulseTicketMicroserviceGatewayJwtSecretKey2026EnterpriseEdition8899}")
    private String jwtSecret;

    /**
     * 允许未登录用户直接访问的公开接口白名单（支持 Ant 通配符匹配）
     * 例如：登录注册、演出公开浏览、公开验票、AI 客服知识库与健康检查接口。
     */
    private static final List<String> WHITELIST_PATHS = List.of(
            "/api/user/auth/**",
            "/api/show/public/**",
            "/api/order/public/**",
            "/api/agent/health",
            "/api/agent/rag/**",
            "/actuator/**"
    );

    /**
     * 网关全局拦截逻辑：每个进入系统的外部请求都会先经过这里
     * 大白话流程：先拦内部接口 -> 再看是不是白名单 -> 有 JWT 就验签解包 -> 洗掉伪造头再塞进真实身份头往下游传。
     */
    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        String path = request.getURI().getPath();
        // 为本次请求生成全局唯一的链路追踪 ID，方便后面跨微服务查日志排障
        String traceId = "tr-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);

        // 1. 严禁外部通过网关直接访问内部 Feign 接口（/internal/** 只允许微服务之间内网互相调）
        if (pathMatcher.match("/internal/**", path) || pathMatcher.match("/**/internal/**", path)) {
            return writeJsonError(exchange.getResponse(), HttpStatus.FORBIDDEN, 403, "禁止外部直接访问微服务内部接口", traceId);
        }

        // 2. 检查是否命中公开白名单
        boolean isWhitelisted = WHITELIST_PATHS.stream().anyMatch(p -> pathMatcher.match(p, path));
        String authHeader = request.getHeaders().getFirst(HttpHeaders.AUTHORIZATION);

        // 如果是白名单接口且前端没带 Token：直接放行，但必须先把客户端可能恶搞伪造的 X-User-* 请求头删干净
        if (isWhitelisted && (authHeader == null || !authHeader.startsWith("Bearer "))) {
            ServerHttpRequest sanitized = request.mutate()
                    .headers(h -> {
                        h.remove(UserContext.HEADER_USER_ID);
                        h.remove(UserContext.HEADER_USERNAME);
                        h.remove(UserContext.HEADER_USER_ROLE);
                        h.set(UserContext.HEADER_TRACE_ID, traceId);
                    })
                    .build();
            return chain.filter(exchange.mutate().request(sanitized).build());
        }

        // 3. 非白名单接口必须携带标准的 Authorization: Bearer <token> 请求头，否则直接打回 401
        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            return writeJsonError(exchange.getResponse(), HttpStatus.UNAUTHORIZED, 401, "请先登录账号", traceId);
        }

        String token = authHeader.substring(7).trim();
        try {
            // 用共享密钥校验 JWT 签名是否合法、是否过期，并取出载荷里的用户信息
            Claims claims = JwtUtils.parseToken(token, jwtSecret);
            String userId = String.valueOf(claims.get("userId"));
            String username = String.valueOf(claims.get("username"));
            Object roleClaim = claims.get("role");
            // 兼容老 Token 没有 role 字段的情况，默认按普通用户 USER 处理
            String role = roleClaim != null ? String.valueOf(roleClaim) : "USER";

            // 4. 先清除客户端可能伪造的同名头，再注入可信身份与角色头透传给下游服务
            // 为什么一定要先 remove 再 set？因为外部黑客可能在 Postman 里自己传 X-User-Role: ADMIN，不清理就会越权！
            ServerHttpRequest mutatedRequest = request.mutate()
                    .headers(headers -> {
                        headers.remove(UserContext.HEADER_USER_ID);
                        headers.remove(UserContext.HEADER_USERNAME);
                        headers.remove(UserContext.HEADER_USER_ROLE);
                        headers.set(UserContext.HEADER_USER_ID, userId);
                        headers.set(UserContext.HEADER_USERNAME, username);
                        headers.set(UserContext.HEADER_USER_ROLE, role);
                        headers.set(UserContext.HEADER_TRACE_ID, traceId);
                    })
                    .build();

            return chain.filter(exchange.mutate().request(mutatedRequest).build());
        } catch (Exception ex) {
            log.warn("JWT verification failed on path {}: {}", path, ex.getMessage());
            // 贴心降级：如果访问的是白名单接口（比如浏览演出列表），即使前端本地存了个过期的旧 Token，也当游客放行，不报错拦住用户
            if (isWhitelisted) {
                ServerHttpRequest sanitized = request.mutate()
                        .headers(h -> {
                            h.remove(UserContext.HEADER_USER_ID);
                            h.remove(UserContext.HEADER_USERNAME);
                            h.remove(UserContext.HEADER_USER_ROLE);
                            h.set(UserContext.HEADER_TRACE_ID, traceId);
                        })
                        .build();
                return chain.filter(exchange.mutate().request(sanitized).build());
            }
            // 非白名单接口 Token 失效或被篡改，直接返回 401 让前端跳登录页
            return writeJsonError(exchange.getResponse(), HttpStatus.UNAUTHORIZED, 401, "登录凭证已过期，请重新登录", traceId);
        }
    }

    /**
     * 在 WebFlux 网关层直接写出统一格式的 JSON 错误响应（和下游微服务的 Result 结构保持一致）
     */
    private Mono<Void> writeJsonError(ServerHttpResponse response, HttpStatus status, int code, String message, String traceId) {
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
        try {
            Result<Void> body = new Result<>(code, message, null, traceId);
            byte[] bytes = objectMapper.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
            DataBuffer buffer = response.bufferFactory().wrap(bytes);
            return response.writeWith(Mono.just(buffer));
        } catch (Exception e) {
            return response.setComplete();
        }
    }

    /**
     * 过滤器执行优先级：数值越小越靠前执行。设为 -100 确保在路由转发之前最先完成鉴权。
     */
    @Override
    public int getOrder() {
        return -100;
    }
}
