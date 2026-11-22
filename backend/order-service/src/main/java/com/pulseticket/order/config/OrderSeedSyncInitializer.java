package com.pulseticket.order.config;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * 启动时幂等对齐历史演示已售席位对应的已支付订单记录（基于 order_no 唯一索引，绝不覆盖或冲突现有订单）
 */
@Component
@RequiredArgsConstructor
public class OrderSeedSyncInitializer {

    private static final Logger log = LoggerFactory.getLogger(OrderSeedSyncInitializer.class);

    private final JdbcTemplate jdbcTemplate;

    @PostConstruct
    public void syncHistoricalSeededOrders() {
        ensureIdempotencySchema();

        String sql = """
                INSERT IGNORE INTO ticket_order (
                    order_no, user_id, username, show_id, show_title, venue_name,
                    session_id, session_title, start_time, seat_ids_json, seat_labels_json,
                    seat_count, total_amount, status, delay_seconds, seata_xid, ticket_code,
                    expire_at, paid_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PAID', 900, ?, ?, ?, ?, ?)
                """;

        int n1 = jdbcTemplate.update(
                sql,
                "TK202610200002", 1002L, "linfeng", 1L,
                "「星际回响 · PulseLive」2026 全息交响电音巡演", "梅赛德斯-奔驰文化中心 · 主场馆",
                101L, "11月15日 周六 19:30 首演黄金场", "2026-11-15 19:30:00",
                "[12,13]", "[\"内场至尊VIP区 2排04座\",\"内场至尊VIP区 2排05座\"]",
                2, "2560.00", "127.0.0.1:8091:20261020888002", "PT-6612-0002",
                "2026-10-20 21:15:00", "2026-10-20 21:03:08", "2026-10-20 21:00:00"
        );
        int n2 = jdbcTemplate.update(
                sql,
                "TK202610210001", 1002L, "linfeng", 2L,
                "百老汇原版音乐剧《歌剧魅影 Phantom》20周年纪念版", "天桥艺术中心 · 大剧场",
                201L, "11月20日 周五 19:30 首演礼宾场", "2026-11-20 19:30:00",
                "[100,101]", "[\"内场至尊VIP区 1排04座\",\"内场至尊VIP区 1排05座\"]",
                2, "1960.00", "127.0.0.1:8091:20261021888001", "PT-7520-0001",
                "2026-10-21 19:45:00", "2026-10-21 19:32:45", "2026-10-21 19:30:00"
        );
        int n3 = jdbcTemplate.update(
                sql,
                "TK202610220001", 1003L, "suwan", 3L,
                "《星际穿越 Interstellar》十周年 IMAX GT 激光重映礼", "万象城 IMAX GT 巨幕影厅",
                301L, "11月08日 周六 20:00 IMAX GT 零点纪念场", "2026-11-08 20:00:00",
                "[164,165]", "[\"中轴声场优选区 3排04座\",\"中轴声场优选区 3排05座\"]",
                2, "278.00", "127.0.0.1:8091:20261022888001", "PT-9104-0001",
                "2026-10-22 22:15:00", "2026-10-22 22:04:19", "2026-10-22 22:00:00"
        );

        if (n1 + n2 + n3 > 0) {
            log.info("[OrderSeedSync] Backfilled {} historical seeded paid orders for box-office consistency", n1 + n2 + n3);
        }
    }

    /**
     * 为已有数据库幂等补齐 idempotency_key 字段与 (user_id, idempotency_key) 唯一索引
     */
    private void ensureIdempotencySchema() {
        try {
            Integer colCount = jdbcTemplate.queryForObject(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                            + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ticket_order' AND COLUMN_NAME = 'idempotency_key'",
                    Integer.class
            );
            if (colCount == null || colCount == 0) {
                jdbcTemplate.execute(
                        "ALTER TABLE ticket_order ADD COLUMN idempotency_key VARCHAR(128) DEFAULT NULL "
                                + "COMMENT '外部请求幂等键(防网关/Agent超时重试重复下单)' AFTER order_no"
                );
                log.info("[OrderSeedSync] Added column idempotency_key to ticket_order");
            }

            Integer idxCount = jdbcTemplate.queryForObject(
                    "SELECT COUNT(*) FROM information_schema.STATISTICS "
                            + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ticket_order' AND INDEX_NAME = 'uk_user_idempotency'",
                    Integer.class
            );
            if (idxCount == null || idxCount == 0) {
                jdbcTemplate.execute(
                        "ALTER TABLE ticket_order ADD UNIQUE INDEX uk_user_idempotency (user_id, idempotency_key)"
                );
                log.info("[OrderSeedSync] Added unique index uk_user_idempotency on ticket_order");
            }
        } catch (Exception e) {
            log.warn("[OrderSeedSync] Idempotency schema check warning: {}", e.getMessage());
        }
    }
}
