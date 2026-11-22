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
