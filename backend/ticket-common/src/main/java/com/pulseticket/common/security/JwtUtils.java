package com.pulseticket.common.security;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.Map;

public final class JwtUtils {

    private static final String DEFAULT_SECRET = "PulseTicketMicroserviceGatewayJwtSecretKey2026EnterpriseEdition8899";
    private static final long EXPIRATION_MS = 7 * 24 * 60 * 60 * 1000L; // 7 days persistent session

    private JwtUtils() {}

    private static SecretKey buildKey(String secret) {
        String keyStr = (secret != null && secret.length() >= 32) ? secret : DEFAULT_SECRET;
        return Keys.hmacShaKeyFor(keyStr.getBytes(StandardCharsets.UTF_8));
    }

    public static String generateToken(Long userId, String username, String vipLevel, String secret) {
        return generateToken(userId, username, vipLevel, "USER", secret);
    }

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

    public static Claims parseToken(String token, String secret) {
        return Jwts.parser()
                .verifyWith(buildKey(secret))
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }
}
