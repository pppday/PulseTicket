package com.pulseticket.show.service;

import com.pulseticket.common.exception.BizException;
import com.pulseticket.show.entity.SeatInventory;
import com.pulseticket.show.entity.ShowInfo;
import com.pulseticket.show.entity.ShowSession;
import com.pulseticket.show.mapper.SeatInventoryMapper;
import com.pulseticket.show.mapper.ShowInfoMapper;
import com.pulseticket.show.mapper.ShowSessionMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.redisson.api.RedissonClient;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ShowCacheAndSeatServiceTest {

    @Mock
    private ShowInfoMapper showInfoMapper;
    @Mock
    private ShowSessionMapper showSessionMapper;
    @Mock
    private SeatInventoryMapper seatInventoryMapper;
    @Mock
    private StringRedisTemplate redisTemplate;
    @Mock
    private RedissonClient redissonClient;
    @Mock
    private JdbcTemplate jdbcTemplate;

    @InjectMocks
    private ShowCacheAndSeatService showService;

    @Test
    void quoteSessionSeatsComputesAuthoritativeTotalFromDb() {
        ShowSession session = ShowSession.builder()
                .id(101L)
                .showId(1L)
                .sessionTitle("11月15日 周六 19:30 首演黄金场")
                .startTime(LocalDateTime.of(2026, 11, 15, 19, 30))
                .build();
        ShowInfo show = ShowInfo.builder()
                .id(1L)
                .title("「星际回响 · PulseLive」2026 全息交响电音巡演")
                .venueName("梅赛德斯-奔驰文化中心 · 主场馆")
                .status("ON_SALE")
                .build();
        List<SeatInventory> seats = List.of(
                SeatInventory.builder()
                        .id(20L).sessionId(101L).seatLabel("3排04座").zoneName("中轴声场优选区")
                        .price(new BigDecimal("680.00")).status("AVAILABLE").build(),
                SeatInventory.builder()
                        .id(21L).sessionId(101L).seatLabel("3排05座").zoneName("中轴声场优选区")
                        .price(new BigDecimal("680.00")).status("AVAILABLE").build()
        );

        when(showSessionMapper.selectById(101L)).thenReturn(session);
        when(showInfoMapper.selectById(1L)).thenReturn(show);
        when(seatInventoryMapper.selectList(any())).thenReturn(seats);

        Map<String, Object> quote = showService.quoteSessionSeats(101L, List.of(21L, 20L));

        assertEquals(new BigDecimal("1360.00"), quote.get("totalAmount"));
        assertEquals(List.of(20L, 21L), quote.get("seatIds"));
        assertEquals(List.of("中轴声场优选区 3排04座", "中轴声场优选区 3排05座"), quote.get("seatLabels"));
    }

    @Test
    void quoteSessionSeatsRejectsLockedOrSoldSeat() {
        ShowSession session = ShowSession.builder().id(101L).showId(1L).build();
        ShowInfo show = ShowInfo.builder().id(1L).status("ON_SALE").build();
        List<SeatInventory> seats = List.of(
                SeatInventory.builder()
                        .id(4L).sessionId(101L).seatLabel("1排04座").zoneName("VIP")
                        .price(new BigDecimal("1280.00")).status("SOLD").build()
        );

        when(showSessionMapper.selectById(101L)).thenReturn(session);
        when(showInfoMapper.selectById(1L)).thenReturn(show);
        when(seatInventoryMapper.selectList(any())).thenReturn(seats);

        BizException ex = assertThrows(BizException.class,
                () -> showService.quoteSessionSeats(101L, List.of(4L)));
        assertEquals(409, ex.getCode());
    }

    @Test
    void quoteSessionSeatsRejectsOffShelfShow() {
        ShowSession session = ShowSession.builder().id(101L).showId(1L).build();
        ShowInfo show = ShowInfo.builder().id(1L).status("OFF_SHELF").build();

        when(showSessionMapper.selectById(101L)).thenReturn(session);
        when(showInfoMapper.selectById(1L)).thenReturn(show);

        BizException ex = assertThrows(BizException.class,
                () -> showService.quoteSessionSeats(101L, List.of(1L)));
        assertEquals(409, ex.getCode());
    }
}
