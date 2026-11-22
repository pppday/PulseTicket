package com.pulseticket.show.config;

import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class ShowOssSchemaInitializer implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(ShowOssSchemaInitializer.class);

    private final JdbcTemplate jdbcTemplate;

    @Override
    public void run(ApplicationArguments args) {
        try {
            Integer colCount = jdbcTemplate.queryForObject(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                            + "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'show_info' AND COLUMN_NAME = 'poster_url'",
                    Integer.class
            );
            if (colCount == null || colCount == 0) {
                jdbcTemplate.execute(
                        "ALTER TABLE show_info ADD COLUMN poster_url VARCHAR(500) DEFAULT NULL "
                                + "COMMENT '阿里云OSS演出海报URL' AFTER poster_gradient"
                );
                log.info("[ShowOssSchemaInitializer] Added column poster_url to show_info table.");
            }

            jdbcTemplate.execute(
                    """
                    CREATE TABLE IF NOT EXISTS venue_dict (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        city VARCHAR(64) NOT NULL COMMENT '所在城市',
                        venue_name VARCHAR(160) NOT NULL COMMENT '场馆名称',
                        hall_name VARCHAR(120) NOT NULL COMMENT '默认演艺厅名称',
                        category_hint VARCHAR(32) DEFAULT 'CONCERT' COMMENT '推荐演出类型',
                        sort_order INT NOT NULL DEFAULT 100 COMMENT '排序权重',
                        UNIQUE KEY uk_city_venue (city, venue_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='城市与演艺场馆字典表'
                    """
            );

            String[][] seedVenues = {
                    {"上海", "梅赛德斯-奔驰文化中心 · 主场馆", "中央全息环形演艺厅", "CONCERT", "10"},
                    {"上海", "上海文化广场 · 主剧场", "2000座交响音乐剧大厅", "MUSICAL", "11"},
                    {"上海", "上海体育场 · 八万人主赛场", "内场环幕巨型舞台", "CONCERT", "12"},
                    {"上海", "MAO Livehouse 上海", "B1 沉浸电音现场厅", "LIVEHOUSE", "13"},
                    {"北京", "天桥艺术中心 · 大剧场", "1600座歌剧大剧场", "MUSICAL", "20"},
                    {"北京", "国家大剧院 · 歌剧院", "中央金顶歌剧厅", "MUSICAL", "21"},
                    {"北京", "华熙 LIVE · 五棵松体育馆", "中央四面台演艺馆", "CONCERT", "22"},
                    {"北京", "疆进酒 · OMNI SPACE", "主舞台 Live 演艺厅", "LIVEHOUSE", "23"},
                    {"深圳", "万象城 IMAX GT 巨幕影厅", "1号 激光 IMAX GT 巨幕厅", "IMAX_MOVIE", "30"},
                    {"深圳", "深圳湾体育中心「春茧」体育馆", "主场馆全息舞台", "CONCERT", "31"},
                    {"深圳", "深圳滨海艺术中心 · 歌剧厅", "歌剧交响主厅", "MUSICAL", "32"},
                    {"杭州", "大麦 66 Livehouse", "全息声学主舞台", "LIVEHOUSE", "40"},
                    {"杭州", "杭州奥体中心「大莲花」体育场", "主赛场环幕舞台", "CONCERT", "41"},
                    {"杭州", "杭州大剧院 · 歌剧院", "主歌剧厅", "MUSICAL", "42"},
                    {"广州", "宝能广州国际体育演艺中心", "主场馆中央舞台", "CONCERT", "50"},
                    {"广州", "广州大剧院 · 歌剧厅", "不对称声学歌剧厅", "MUSICAL", "51"},
                    {"成都", "成都东安湖体育公园多功能体育馆", "中央全息演艺厅", "CONCERT", "60"},
                    {"成都", "成都城市音乐厅 · 歌剧厅", "特大型声学歌剧厅", "MUSICAL", "61"}
            };

            for (String[] row : seedVenues) {
                jdbcTemplate.update(
                        "INSERT IGNORE INTO venue_dict (city, venue_name, hall_name, category_hint, sort_order) VALUES (?, ?, ?, ?, ?)",
                        row[0], row[1], row[2], row[3], Integer.parseInt(row[4])
                );
            }

            // 同步已有 show_info 与 show_session 中的真实城市与场馆数据
            jdbcTemplate.execute(
                    """
                    INSERT IGNORE INTO venue_dict (city, venue_name, hall_name, category_hint, sort_order)
                    SELECT s.city,
                           s.venue_name,
                            COALESCE((SELECT ss.hall_name FROM show_session ss WHERE ss.show_id = s.id LIMIT 1), '中央全息沉浸演艺厅'),
                           s.category,
                           50
                    FROM show_info s
                    WHERE s.city IS NOT NULL AND s.venue_name IS NOT NULL
                    """
            );
        } catch (Exception e) {
            log.warn("[ShowOssSchemaInitializer] Schema check warning: {}", e.getMessage());
        }
    }
}
