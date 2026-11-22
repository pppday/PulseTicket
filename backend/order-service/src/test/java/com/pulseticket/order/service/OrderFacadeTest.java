package com.pulseticket.order.service;

import com.pulseticket.common.context.UserContext;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.feign.client.ShowSeatClient;
import com.pulseticket.order.controller.OrderController;
import com.pulseticket.order.entity.TicketOrder;
import com.pulseticket.order.mapper.MqEventLogMapper;
import com.pulseticket.order.mapper.SeataTxAuditMapper;
import com.pulseticket.order.mapper.TicketOrderMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderFacadeTest {

    @Mock
    private RedissonClient redissonClient;
    @Mock
    private OrderTxService orderTxService;
    @Mock
    private TicketOrderMapper ticketOrderMapper;
    @Mock
    private SeataTxAuditMapper seataTxAuditMapper;
    @Mock
    private MqEventLogMapper mqEventLogMapper;
    @Mock
    private SeataAuditService seataAuditService;
    @Mock
    private ShowSeatClient showSeatClient;
    @Mock
    private RabbitTemplate rabbitTemplate;
    @Mock
    private JdbcTemplate jdbcTemplate;
    @Mock
    private RLock userLock;
    @Mock
    private RLock seatLock;

    @InjectMocks
    private OrderFacade orderFacade;

    @BeforeEach
    void setUp() {
        UserContext.clear();
    }

    @AfterEach
    void tearDown() {
        UserContext.clear();
    }

    @Test
    void idempotentRetryReturnsExistingOrderWithoutExecutingTx() {
        when(redissonClient.getLock("lock:order:submit:1001:101")).thenReturn(userLock);
        when(redissonClient.getLock("lock:seat:101:4")).thenReturn(seatLock);
        when(redissonClient.getLock("lock:seat:101:5")).thenReturn(seatLock);
        when(userLock.tryLock()).thenReturn(true);
        when(userLock.isHeldByCurrentThread()).thenReturn(true);

        TicketOrder existing = TicketOrder.builder()
                .id(10L)
                .orderNo("TK202611160001")
                .userId(1001L)
                .sessionId(101L)
                .seatIdsJson("[4,5]")
                .totalAmount(new BigDecimal("2560.00"))
                .status("UNPAID")
                .idempotencyKey("thread-1:call-abc")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(existing);

        TicketOrder result = orderFacade.createOrderWithThreeLayerDefense(
                1001L, "zhaolan", 101L, List.of(5L, 4L), false, false, "thread-1:call-abc"
        );

        assertSame(existing, result);
        verifyNoInteractions(orderTxService);
        verify(userLock).unlock();
    }

    @Test
    void idempotentConflictThrows409WhenSeatIdsDiffer() {
        when(redissonClient.getLock("lock:order:submit:1001:101")).thenReturn(userLock);
        when(redissonClient.getLock("lock:seat:101:4")).thenReturn(seatLock);
        when(redissonClient.getLock("lock:seat:101:6")).thenReturn(seatLock);
        when(userLock.tryLock()).thenReturn(true);

        TicketOrder existing = TicketOrder.builder()
                .orderNo("TK202611160001")
                .userId(1001L)
                .sessionId(101L)
                .seatIdsJson("[4,5]")
                .idempotencyKey("thread-1:call-abc")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(existing);

        BizException ex = assertThrows(BizException.class, () ->
                orderFacade.createOrderWithThreeLayerDefense(
                        1001L, "zhaolan", 101L, List.of(4L, 6L), false, false, "thread-1:call-abc"
                )
        );
        assertEquals(409, ex.getCode());
        verifyNoInteractions(orderTxService);
    }

    @Test
    void duplicateKeyExceptionFallsBackToExistingOrder() throws Exception {
        when(redissonClient.getLock("lock:order:submit:1001:101")).thenReturn(userLock);
        when(redissonClient.getLock("lock:seat:101:4")).thenReturn(seatLock);
        when(userLock.tryLock()).thenReturn(true);
        when(seatLock.tryLock(anyLong(), anyLong(), any())).thenReturn(true);

        TicketOrder existing = TicketOrder.builder()
                .orderNo("TK202611160009")
                .userId(1001L)
                .sessionId(101L)
                .seatIdsJson("[4]")
                .status("UNPAID")
                .idempotencyKey("thread-2:call-xyz")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(null).thenReturn(existing);
        when(orderTxService.executeCreateOrderGlobalTx(
                anyString(), eq(1001L), eq("zhaolan"), eq(101L), eq(List.of(4L)),
                eq(900), eq(false), eq("thread-2:call-xyz")
        )).thenThrow(new DuplicateKeyException("uk_user_idempotency"));

        TicketOrder result = orderFacade.createOrderWithThreeLayerDefense(
                1001L, "zhaolan", 101L, List.of(4L), false, false, "thread-2:call-xyz"
        );
        assertEquals("TK202611160009", result.getOrderNo());
    }

    @Test
    void cancelPaidOrderThrows409InsteadOfReturningFakeSuccess() {
        TicketOrder paid = TicketOrder.builder()
                .id(1L)
                .orderNo("TK202610200001")
                .userId(1001L)
                .status("PAID")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(paid);

        BizException ex = assertThrows(BizException.class,
                () -> orderFacade.cancelOrderManually(1001L, "TK202610200001"));
        assertEquals(409, ex.getCode());
        verify(ticketOrderMapper, never()).casCloseUnpaidOrder(anyString(), anyString());
    }

    @Test
    void cancelTimeoutClosedOrderThrows409() {
        TicketOrder closed = TicketOrder.builder()
                .id(2L)
                .orderNo("TK202610200099")
                .userId(1001L)
                .status("CLOSED")
                .closeReason("TIMEOUT_UNPAID")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(closed);

        BizException ex = assertThrows(BizException.class,
                () -> orderFacade.cancelOrderManually(1001L, "TK202610200099"));
        assertEquals(409, ex.getCode());
    }

    @Test
    void cancelAlreadyUserCancelledOrderIsIdempotent() {
        TicketOrder cancelled = TicketOrder.builder()
                .id(3L)
                .orderNo("TK202610200088")
                .userId(1001L)
                .status("CLOSED")
                .closeReason("USER_CANCEL")
                .build();
        when(ticketOrderMapper.selectOne(any())).thenReturn(cancelled);

        TicketOrder res = orderFacade.cancelOrderManually(1001L, "TK202610200088");
        assertSame(cancelled, res);
        verify(ticketOrderMapper, never()).casCloseUnpaidOrder(anyString(), anyString());
    }

    @Test
    void orderControllerRejectsMissingUserIdWith401() {
        OrderController controller = new OrderController(orderFacade, ticketOrderMapper);
        BizException ex = assertThrows(BizException.class, controller::listMyOrders);
        assertEquals(401, ex.getCode());
    }
}
