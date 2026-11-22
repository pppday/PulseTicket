package com.pulseticket.show.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@TableName("show_session")
public class ShowSession {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long showId;
    private String sessionCode;
    private String sessionTitle;
    private String hallName;
    private LocalDateTime startTime;
    private LocalDateTime endTime;
    private Integer totalSeats;
    private Integer availableSeats;
    private Integer lockedSeats;
    private Integer soldSeats;
    private Integer version;
    private LocalDateTime createdAt;
}
