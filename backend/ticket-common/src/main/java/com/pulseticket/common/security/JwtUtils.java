package com.pulseticket.common.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.Map;

/**
 * JWT（JSON Web Token）签发与验签工具类
 * 作用：
 * 1. 用户在 user-service 登录/注册成功时，调用 generateToken 签发一张带防伪签名的 7 天有效“通行证”；
 * 2. 每次请求到达网关 ticket-gateway 的 AuthGlobalFilter 时，调用 parseToken 验签并取出里面的 userId、username、role。
 * 采用 HMAC-SHA 对称签名算法，网关与用户服务共用同一把密钥即可无状态校验，不用每次查数据库。
 */
public final class JwtUtils {

    /** 兜底默认密钥（当配置未提供或长度不足 32 字节时启用，满足 HS256 最小密钥长度要求） */
    private static final String DEFAULT_SECRET = "PulseTicketMicroserviceGatewayJwtSecretKey2026EnterpriseEdition8899";
    /** Token 有效期：7 天，避免用户频繁重新登录 */
    private static final long EXPIRATION_MS = 7 * 24 * 60 * 60 * 1000L; // 7 days persistent session

    private JwtUtils() {}

    /** 根据配置密钥构造 HMAC-SHA 签名密钥对象 */
    private static SecretKey buildKey(String secret) {
        String keyStr = (secret != null && secret.length() >= 32) ? secret : DEFAULT_SECRET;
        return Keys.hmacShaKeyFor(keyStr.getBytes(StandardCharsets.UTF_8));
    }

    /** 签发普通用户角色（默认 USER）的 JWT Token */
    public static String generateToken(Long userId, String username, String vipLevel, String secret) {
        return generateToken(userId, username, vipLevel, "USER", secret);
    }

    /**
     * 签发带完整身份信息（用户 ID、用户名、会员等级、RBAC 角色）的 JWT Token
     * 签发后任何人都无法篡改里面的 role 或 userId，否则验签直接失败。
     */
    public static String generateToken(Long userId, String username, String vipLevel, String role, String secret) {
        long now = System.currentTimeMillis();
        return Jwts.builder()
                .subject(String.valueOf(userId))
                .claims(Map.of(
                        "userId", userId,
                        "username", username,
                        "vipLevel", vipLevel != null ? vipLevel : "NORMAL",
                        "role", role != null ? role : "USER"
                ))
                .issuedAt(new Date(now))
                .expiration(new Date(now + EXPIRATION_MS))
                .signWith(buildKey(secret))
                .compact();
    }

    /**
     * 校验 JWT 签名与有效期，并解析出里面的 Claims 载荷数据
     * 如果 Token 被篡改、格式错误或已过期，JJWT 会直接抛出异常。
     */
    public static Claims parseToken(String token, String secret) {
        return Jwts.parser()
                .verifyWith(buildKey(secret))
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }
}
