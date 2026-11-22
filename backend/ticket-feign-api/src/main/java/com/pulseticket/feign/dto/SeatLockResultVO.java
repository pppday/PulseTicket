package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class SeatLockResultVO implements Serializable {

    private String orderNo;
    private Long showId;
    private String showTitle;
    private String venueName;
    private Long sessionId;
    private String sessionTitle;
    private String startTime;
    private List<Long> seatIds;
    private List<String> seatLabels;
    private BigDecimal totalAmount;
}
