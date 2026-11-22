package com.pulseticket.user.config;

import com.pulseticket.user.entity.UserAccount;
import com.pulseticket.user.mapper.UserAccountMapper;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

/**
 * 启动时幂等确保 RBAC 角色列存在、预置架构运维专员账号 (admin)、将历史脱敏手机号升级为真实 11 位手机号，并将明文密码统一升级为 BCrypt 哈希
 */
@Component
@RequiredArgsConstructor
public class UserRbacSchemaInitializer {

    private static final Logger log = LoggerFactory.getLogger(UserRbacSchemaInitializer.class);

    private static final Map<String, String> SEED_REAL_PHONES = Map.of(
            "admin", "18800000001",
            "zhaolan", "13820240336",
            "linfeng", "13910868821",
            "suwan", "13760125519"
    );

    private final JdbcTemplate jdbcTemplate;
    private final UserAccountMapper userAccountMapper;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    @PostConstruct
    public void ensureRbacSchemaAndSeed() {
        Integer colCount = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_account' AND COLUMN_NAME = 'role'",
                Integer.class
        );
        if (colCount == null || colCount == 0) {
            jdbcTemplate.execute(
                    "ALTER TABLE user_account ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'USER' COMMENT '角色权限: USER / ADMIN' AFTER vip_level"
            );
            log.info("[RBAC] Added role column to user_account");
        }

        String adminHash = passwordEncoder.encode("Admin@2026");
        jdbcTemplate.update(
                """
                INSERT INTO user_account (id, username, password_hash, nickname, phone, vip_level, role, points_balance, total_orders)
                VALUES (1000, 'admin', ?, '系统架构师', '18800000001', 'BLACK_DIAMOND', 'ADMIN', 9999, 0)
                ON DUPLICATE KEY UPDATE role = 'ADMIN', nickname = '系统架构师'
                """,
                adminHash
        );

        // 将历史明文演示密码幂等升级为真实 BCrypt 哈希，并将历史脱敏手机号幂等升级为真实 11 位手机号
        List<UserAccount> allUsers = userAccountMapper.selectList(null);
        for (UserAccount u : allUsers) {
            boolean dirty = false;
            String ph = u.getPasswordHash();
            if (ph != null && !ph.startsWith("$2a$") && !ph.startsWith("$2b$")) {
                u.setPasswordHash(passwordEncoder.encode(ph));
                dirty = true;
                log.info("[RBAC] Upgraded password hash to BCrypt for user={}", u.getUsername());
            }

            String phone = u.getPhone();
            if (phone == null || phone.isBlank() || phone.contains("*") || !phone.matches("^1[3-9]\\d{9}$")) {
                String upgradedPhone = SEED_REAL_PHONES.get(u.getUsername());
                if (upgradedPhone == null) {
                    if (phone != null && phone.matches("^1[3-9]\\d\\*{4}\\d{4}$")) {
                        upgradedPhone = phone.replace("****", "0168");
                    } else {
                        long tail = Math.abs(u.getId() != null ? u.getId() : 1000L) % 100000000L;
                        upgradedPhone = String.format("139%08d", tail);
                    }
                }
                u.setPhone(upgradedPhone);
                dirty = true;
                log.info("[UserPhone] Upgraded legacy masked phone to real 11-digit phone for user={}", u.getUsername());
            }

            if (dirty) {
                userAccountMapper.updateById(u);
            }
        }
    }
}
