package com.pulseticket.user.service;

import com.pulseticket.feign.dto.OrderRefundedEventDTO;
import com.pulseticket.user.entity.PointsLog;
import com.pulseticket.user.entity.UserAccount;
import com.pulseticket.user.mapper.PointsLogMapper;
import com.pulseticket.user.mapper.UserAccountMapper;
import com.pulseticket.user.mapper.UserMessageMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.DuplicateKeyException;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UserPointsServiceTest {

    @Mock
    private UserAccountMapper userAccountMapper;
    @Mock
    private PointsLogMapper pointsLogMapper;
    @Mock
    private UserMessageMapper userMessageMapper;

    @InjectMocks
    private UserPointsService userPointsService;

    @Test
    void refundDeductsPointsAndRecordsNegativeLedgerEntry() {
        PointsLog originalReward = new PointsLog();
        originalReward.setOrderNo("TK202611240001");
        originalReward.setBizType("ORDER_PAID_REWARD");
        originalReward.setPointsDelta(2140);

        UserAccount user = new UserAccount();
        user.setId(1001L);
        user.setPointsBalance(3500);

        when(pointsLogMapper.selectOne(any())).thenReturn(originalReward);
        when(userAccountMapper.selectById(1001L)).thenReturn(user);

        OrderRefundedEventDTO event = new OrderRefundedEventDTO();
        event.setOrderNo("TK202611240001");
        event.setUserId(1001L);
        event.setShowTitle("歌剧魅影");
        event.setTotalAmount(new BigDecimal("2140.00"));
        event.setFeeAmount(new BigDecimal("214.00"));
        event.setFeeRate("10%");
        event.setRefundAmount(new BigDecimal("1926.00"));
        event.setTierName("标准退票期");
        event.setPointsToDeduct(2140);

        boolean processed = userPointsService.processOrderRefundDeductIdempotent(event);

        assertTrue(processed);
        ArgumentCaptor<PointsLog> captor = ArgumentCaptor.forClass(PointsLog.class);
        verify(pointsLogMapper).insert(captor.capture());
        assertEquals("ORDER_REFUND_DEDUCT", captor.getValue().getBizType());
        assertEquals(-2140, captor.getValue().getPointsDelta());
        assertEquals(1360, captor.getValue().getBalanceAfter());
        verify(userAccountMapper).deductPointsAndOrderCount(1001L, 2140);
        verify(userMessageMapper).insert(any());
    }

    @Test
    void duplicateRefundMessageIsIdempotentlySkipped() {
        UserAccount user = new UserAccount();
        user.setId(1001L);
        user.setPointsBalance(2000);
        when(pointsLogMapper.selectOne(any())).thenReturn(null);
        when(userAccountMapper.selectById(1001L)).thenReturn(user);
        when(pointsLogMapper.insert(any())).thenThrow(new DuplicateKeyException("uk_order_biz"));

        OrderRefundedEventDTO event = new OrderRefundedEventDTO();
        event.setOrderNo("TK202611240001");
        event.setUserId(1001L);
        event.setTotalAmount(new BigDecimal("1000.00"));
        event.setPointsToDeduct(1000);

        boolean processed = userPointsService.processOrderRefundDeductIdempotent(event);

        assertFalse(processed);
        verify(userAccountMapper, never()).deductPointsAndOrderCount(anyLong(), anyInt());
        verify(userMessageMapper, never()).insert(any());
    }
}
