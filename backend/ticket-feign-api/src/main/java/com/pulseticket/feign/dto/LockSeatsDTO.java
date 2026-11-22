package com.pulseticket.feign.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class LockSeatsDTO implements Serializable {

    private String orderNo;
    private Long userId;
    private Long sessionId;
    private List<Long> seatIds;
}
