package com.pulseticket.show.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 演出基础信息实体（映射 `ticket_show.show_info` 表）。
 * 记录演出标题、分类、城市、场馆、OSS 海报链接、票价区间、热度分及上下架状态（ON_SALE / OFF_SHELF）。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("show_info")
public class ShowInfo {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String showCode;
    private String title;
    private String subtitle;
    private String category;
    private String city;
    private String venueName;
    private String posterGradient;
    private String posterUrl;
    private String badgeText;
    private BigDecimal rating;
    private BigDecimal minPrice;
    private BigDecimal maxPrice;
    private String tags;
    private String description;
    private Integer hotScore;
    private String status;
    private LocalDateTime createdAt;
}
