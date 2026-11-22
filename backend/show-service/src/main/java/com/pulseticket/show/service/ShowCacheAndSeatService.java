package com.pulseticket.show.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.feign.dto.LockSeatsDTO;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.OrderPaidEventDTO;
import com.pulseticket.feign.dto.SeatLockResultVO;
import com.pulseticket.show.entity.SeatInventory;
import com.pulseticket.show.entity.ShowInfo;
import com.pulseticket.show.entity.ShowSession;
import com.pulseticket.show.mapper.SeatInventoryMapper;
import com.pulseticket.show.mapper.ShowInfoMapper;
import com.pulseticket.show.mapper.ShowSessionMapper;
import io.seata.core.context.RootContext;
import lombok.RequiredArgsConstructor;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.DayOfWeek;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

@Service
@RequiredArgsConstructor
public class ShowCacheAndSeatService {

    private static final Logger log = LoggerFactory.getLogger(ShowCacheAndSeatService.class);

    public static final String CACHE_SHOW_DETAIL_PREFIX = "ticket:cache:show:";
    public static final String CACHE_SEATMAP_PREFIX = "ticket:cache:seatmap:";
    public static final String LOCK_CACHE_REBUILD_PREFIX = "lock:cache:show:rebuild:";
    public static final String NULL_SENTINEL = "__NULL__";

    private static final DateTimeFormatter DT_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final ShowInfoMapper showInfoMapper;
    private final ShowSessionMapper showSessionMapper;
    private final SeatInventoryMapper seatInventoryMapper;
    private final StringRedisTemplate redisTemplate;
    private final RedissonClient redissonClient;
    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper().registerModule(new JavaTimeModule());

    public List<Map<String, Object>> listShows(String category) {
        return queryShowsCatalog(category, false);
    }

    public List<Map<String, Object>> listAllShowsForAdmin(String category) {
        return queryShowsCatalog(category, true);
    }

    private List<Map<String, Object>> queryShowsCatalog(String category, boolean includeOffShelf) {
        LambdaQueryWrapper<ShowInfo> qw = new LambdaQueryWrapper<>();
        if (!includeOffShelf) {
            qw.ne(ShowInfo::getStatus, "OFF_SHELF");
        }
        if (category != null && !category.isBlank() && !"ALL".equalsIgnoreCase(category)) {
            qw.eq(ShowInfo::getCategory, category.trim().toUpperCase());
        }
        qw.orderByDesc(ShowInfo::getHotScore).orderByDesc(ShowInfo::getId);
        List<ShowInfo> shows = showInfoMapper.selectList(qw);
        List<ShowSession> allSessions = showSessionMapper.selectList(
                new LambdaQueryWrapper<ShowSession>().orderByAsc(ShowSession::getStartTime)
        );

        Map<Long, List<ShowSession>> sessionGroup = new HashMap<>();
        for (ShowSession s : allSessions) {
            sessionGroup.computeIfAbsent(s.getShowId(), k -> new ArrayList<>()).add(s);
        }

        List<Map<String, Object>> result = new ArrayList<>();
        for (ShowInfo show : shows) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("show", show);
            item.put("sessions", sessionGroup.getOrDefault(show.getId(), Collections.emptyList()));
            result.add(item);
        }
        return result;
    }

    /**
     * 热点演出详情查询 — 完整实现 Redis 缓存三座大山防护：
     * 1. 防缓存穿透 (Penetration)：不存在的 ID 缓存 "__NULL__" 空对象 (短 TTL 60s)
     * 2. 防缓存击穿 (Breakdown)：热点 Key 失效时采用 Redisson RLock 互斥锁 + 双重检查锁重建
     * 3. 防缓存雪崩 (Avalanche)：基础 TTL 1800s + 随机抖动值 (60~360s)
     */
    public Map<String, Object> getShowDetailWithCacheProtection(Long showId) {
        String cacheKey = CACHE_SHOW_DETAIL_PREFIX + showId;
        String cachedJson = redisTemplate.opsForValue().get(cacheKey);

        if (cachedJson != null) {
            if (NULL_SENTINEL.equals(cachedJson)) {
                Map<String, Object> guardResp = new LinkedHashMap<>();
                guardResp.put("cacheStatus", "REDIS_NULL_GUARD");
                guardResp.put("message", "触发 Redis 空值哨兵防穿透拦截 (__NULL__, TTL<=60s)，未查询 MySQL");
                guardResp.put("show", null);
                return guardResp;
            }
            try {
                Map<String, Object> data = objectMapper.readValue(cachedJson, new TypeReference<>() {});
                data.put("cacheStatus", "REDIS_HIT");
                data.put("ttlSeconds", redisTemplate.getExpire(cacheKey, TimeUnit.SECONDS));
                return data;
            } catch (Exception ignored) {
            }
        }

        // 缓存未命中：使用 Redisson 互斥锁防止热点 Key 并发击穿 MySQL
        RLock rebuildLock = redissonClient.getLock(LOCK_CACHE_REBUILD_PREFIX + showId);
        rebuildLock.lock();
        try {
            // Double Check 双重检查
            String secondCheck = redisTemplate.opsForValue().get(cacheKey);
            if (secondCheck != null) {
                if (NULL_SENTINEL.equals(secondCheck)) {
                    return Map.of("cacheStatus", "REDIS_NULL_GUARD", "show", Collections.emptyMap());
                }
                Map<String, Object> data = objectMapper.readValue(secondCheck, new TypeReference<>() {});
                data.put("cacheStatus", "REDIS_HIT_AFTER_WAIT");
                return data;
            }

            ShowInfo show = showInfoMapper.selectById(showId);
            if (show == null) {
                // 写入空值哨兵防穿透 (60s 短过期)
                redisTemplate.opsForValue().set(cacheKey, NULL_SENTINEL, Duration.ofSeconds(60));
                Map<String, Object> missResp = new LinkedHashMap<>();
                missResp.put("cacheStatus", "PENETRATION_PROTECTED");
                missResp.put("message", "MySQL 中不存在该演出，已写入 Redis __NULL__ 哨兵 (TTL=60s)");
                missResp.put("show", null);
                return missResp;
            }

            List<ShowSession> sessions = showSessionMapper.selectList(
                    new LambdaQueryWrapper<ShowSession>()
                            .eq(ShowSession::getShowId, showId)
                            .orderByAsc(ShowSession::getStartTime)
            );

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("show", show);
            payload.put("sessions", sessions);

            // 防缓存雪崩：基础 1800s + 随机抖动 60~360s
            long jitterTtl = 1800L + ThreadLocalRandom.current().nextInt(60, 361);
            redisTemplate.opsForValue().set(
                    cacheKey,
                    objectMapper.writeValueAsString(payload),
                    Duration.ofSeconds(jitterTtl)
            );

            payload.put("cacheStatus", "MYSQL_MUTEX_REBUILT");
            payload.put("ttlSeconds", jitterTtl);
            return payload;
        } catch (Exception e) {
            throw new BizException(500, "加载演出详情失败: " + e.getMessage());
        } finally {
            if (rebuildLock.isHeldByCurrentThread()) {
                rebuildLock.unlock();
            }
        }
    }

    /**
     * 服务端权威核价与余座实时校验（供 Agent 确认卡片与结算前调用，绝不采信前端/大模型传入金额）
     */
    public Map<String, Object> quoteSessionSeats(Long sessionId, List<Long> rawSeatIds) {
        if (sessionId == null) {
            throw new BizException(400, "场次编号不能为空");
        }
        if (rawSeatIds == null || rawSeatIds.isEmpty()) {
            throw new BizException(400, "请至少选择 1 个座位");
        }
        List<Long> sortedSeatIds = rawSeatIds.stream().filter(Objects::nonNull).distinct().sorted().toList();
        if (sortedSeatIds.isEmpty()) {
            throw new BizException(400, "座位编号不能为空");
        }
        if (sortedSeatIds.size() > 6) {
            throw new BizException(400, "单笔订单最多支持选座 6 张");
        }

        ShowSession session = showSessionMapper.selectById(sessionId);
        if (session == null) {
            throw new BizException(404, "演出场次不存在: " + sessionId);
        }
        ShowInfo show = showInfoMapper.selectById(session.getShowId());
        if (show == null) {
            throw new BizException(404, "所属演出不存在");
        }
        if ("OFF_SHELF".equalsIgnoreCase(show.getStatus())) {
            throw new BizException(409, "该演出已下架归档，暂不支持购买");
        }
        if (session.getStartTime() != null && session.getStartTime().isBefore(LocalDateTime.now())) {
            throw new BizException(409, "该场次已开演或已结束，停止售票，请选择其他近期场次");
        }

        List<SeatInventory> seats = seatInventoryMapper.selectList(
                new LambdaQueryWrapper<SeatInventory>()
                        .eq(SeatInventory::getSessionId, sessionId)
                        .in(SeatInventory::getId, sortedSeatIds)
                        .orderByAsc(SeatInventory::getId)
        );
        if (seats.size() != sortedSeatIds.size()) {
            throw new BizException(400, "部分座位不存在或不属于场次 #" + sessionId);
        }

        List<String> unavailable = seats.stream()
                .filter(s -> !"AVAILABLE".equals(s.getStatus()))
                .map(s -> s.getSeatLabel() + "(" + s.getStatus() + ")")
                .toList();
        if (!unavailable.isEmpty()) {
            throw new BizException(409, "所选座位已被锁定或售出，请重新选座: " + String.join("、", unavailable));
        }

        BigDecimal totalAmount = BigDecimal.ZERO;
        List<String> seatLabels = new ArrayList<>();
        List<BigDecimal> unitPrices = new ArrayList<>();
        for (SeatInventory seat : seats) {
            totalAmount = totalAmount.add(seat.getPrice());
            seatLabels.add(seat.getZoneName() + " " + seat.getSeatLabel());
            unitPrices.add(seat.getPrice());
        }

        Map<String, Object> res = new LinkedHashMap<>();
        res.put("showId", show.getId());
        res.put("showTitle", show.getTitle());
        res.put("venueName", show.getVenueName());
        res.put("sessionId", session.getId());
        res.put("sessionTitle", session.getSessionTitle());
        res.put("startTime", session.getStartTime() != null ? session.getStartTime().format(DT_FMT) : null);
        res.put("seatIds", sortedSeatIds);
        res.put("seatLabels", seatLabels);
        res.put("unitPrices", unitPrices);
        res.put("seatCount", sortedSeatIds.size());
        res.put("totalAmount", totalAmount);
        return res;
    }

    /**
     * 查询场次实时座位图
     */
    public Map<String, Object> getSessionSeatMap(Long sessionId) {
        ShowSession session = showSessionMapper.selectById(sessionId);
        if (session == null) {
            throw new BizException(404, "演出场次不存在: " + sessionId);
        }
        ShowInfo show = showInfoMapper.selectById(session.getShowId());
        List<SeatInventory> seats = seatInventoryMapper.selectList(
                new LambdaQueryWrapper<SeatInventory>()
                        .eq(SeatInventory::getSessionId, sessionId)
                        .orderByAsc(SeatInventory::getRowNo)
                        .orderByAsc(SeatInventory::getColNo)
        );

        Map<String, Object> res = new LinkedHashMap<>();
        res.put("show", show);
        res.put("session", session);
        res.put("seats", seats);
        return res;
    }

    /**
     * Seata AT 分支事务：锁定所选座位
     * 面试核心考点：
     * 1. 通过 WHERE status = 'AVAILABLE' 实现数据库乐观条件更新兜底；
     * 2. 受影響行数 updated != seatIds.size() 立即抛异常触发全局回滚；
     * 3. 采用 Cache-Aside (先更新数据库，再删除缓存)。
     */
    @Transactional(rollbackFor = Exception.class)
    public SeatLockResultVO lockSeatsInAtTransaction(LockSeatsDTO dto) {
        String xid = RootContext.getXID();
        log.info("[Seata RM show-service] 收到锁座请求 orderNo={}, sessionId={}, seatIds={}, XID={}",
                dto.getOrderNo(), dto.getSessionId(), dto.getSeatIds(), xid);

        if (dto.getSeatIds() == null || dto.getSeatIds().isEmpty()) {
            throw new BizException("请至少选择 1 个座位");
        }

        ShowSession session = showSessionMapper.selectById(dto.getSessionId());
        if (session == null) {
            throw new BizException(404, "场次不存在");
        }
        if (session.getStartTime() != null && session.getStartTime().isBefore(LocalDateTime.now())) {
            throw new BizException(409, "该场次已开演或已结束，停止售票，请选择其他近期场次");
        }
        ShowInfo show = showInfoMapper.selectById(session.getShowId());

        int updated = seatInventoryMapper.lockAvailableSeats(
                dto.getSessionId(),
                dto.getOrderNo(),
                dto.getUserId(),
                dto.getSeatIds()
        );
        if (updated != dto.getSeatIds().size()) {
            throw new BizException(409, "手慢了一步！所选座位刚被其他观众抢占，请重新选座");
        }

        showSessionMapper.syncSessionSeatCounts(dto.getSessionId());

        List<SeatInventory> lockedSeats = seatInventoryMapper.selectBatchIds(dto.getSeatIds());
        lockedSeats.sort(Comparator.comparing(SeatInventory::getId));
        BigDecimal totalAmount = BigDecimal.ZERO;
        List<String> labels = new ArrayList<>();
        for (SeatInventory seat : lockedSeats) {
            totalAmount = totalAmount.add(seat.getPrice());
            labels.add(seat.getZoneName() + " " + seat.getSeatLabel());
        }

        evictShowCaches(session.getShowId(), dto.getSessionId());
        registerPostTxCacheEviction(session.getShowId(), dto.getSessionId());

        SeatLockResultVO vo = new SeatLockResultVO();
        vo.setOrderNo(dto.getOrderNo());
        vo.setShowId(show.getId());
        vo.setShowTitle(show.getTitle());
        vo.setVenueName(show.getVenueName());
        vo.setSessionId(session.getId());
        vo.setSessionTitle(session.getSessionTitle());
        vo.setStartTime(session.getStartTime().format(DT_FMT));
        vo.setSeatIds(dto.getSeatIds());
        vo.setSeatLabels(labels);
        vo.setTotalAmount(totalAmount);
        return vo;
    }

    /**
     * 超时未支付关单、取消或已支付退票时幂等释放座位
     */
    @Transactional(rollbackFor = Exception.class)
    public int releaseSeatsIdempotent(OrderClosedEventDTO dto) {
        int released = "USER_REFUND".equals(dto.getCloseReason())
                ? seatInventoryMapper.releaseSoldSeatsByOrder(dto.getSessionId(), dto.getOrderNo())
                : seatInventoryMapper.releaseLockedSeatsByOrder(dto.getSessionId(), dto.getOrderNo());
        if (released > 0) {
            ShowSession session = showSessionMapper.selectById(dto.getSessionId());
            showSessionMapper.syncSessionSeatCounts(dto.getSessionId());
            if (session != null) {
                evictShowCaches(session.getShowId(), dto.getSessionId());
            }
            log.info("[座位库存已释放] orderNo={}, releasedSeats={}, reason={}",
                    dto.getOrderNo(), released, dto.getCloseReason());
        } else {
            log.info("[座位释放幂等跳过] orderNo={} 座位已释放或未处于锁定态", dto.getOrderNo());
        }
        return released;
    }

    /**
     * 支付成功后幂等确认座位售出 (LOCKED -> SOLD)
     */
    @Transactional(rollbackFor = Exception.class)
    public int confirmSeatsSoldIdempotent(OrderPaidEventDTO dto) {
        int sold = seatInventoryMapper.confirmLockedSeatsSold(dto.getSessionId(), dto.getOrderNo());
        if (sold > 0) {
            showSessionMapper.syncSessionSeatCounts(dto.getSessionId());
            evictShowCaches(dto.getShowId(), dto.getSessionId());
            log.info("[座位状态流转 LOCKED->SOLD] orderNo={}, soldSeats={}", dto.getOrderNo(), sold);
        } else {
            log.info("[座位出票幂等跳过] orderNo={} 座位已处于 SOLD 状态", dto.getOrderNo());
        }
        return sold;
    }

    public void evictShowCaches(Long showId, Long sessionId) {
        if (showId != null) {
            redisTemplate.delete(CACHE_SHOW_DETAIL_PREFIX + showId);
        }
        if (sessionId != null) {
            redisTemplate.delete(CACHE_SEATMAP_PREFIX + sessionId);
        }
    }

    /**
     * ADMIN 专属：场馆票房与席位销售全景统计 (实时聚合 show_info + show_session + seat_inventory)
     */
    public Map<String, Object> getAdminSalesAnalytics() {
        List<ShowInfo> shows = showInfoMapper.selectList(
                new LambdaQueryWrapper<ShowInfo>().orderByAsc(ShowInfo::getId)
        );
        List<ShowSession> sessions = showSessionMapper.selectList(
                new LambdaQueryWrapper<ShowSession>().orderByAsc(ShowSession::getStartTime)
        );
        List<SeatInventory> seats = seatInventoryMapper.selectList(
                new LambdaQueryWrapper<SeatInventory>()
                        .orderByAsc(SeatInventory::getSessionId)
                        .orderByAsc(SeatInventory::getRowNo)
                        .orderByAsc(SeatInventory::getColNo)
        );

        Map<Long, ShowInfo> showMap = new LinkedHashMap<>();
        for (ShowInfo s : shows) {
            showMap.put(s.getId(), s);
        }

        int totalSeats = seats.size();
        int soldSeats = 0;
        int lockedSeats = 0;
        int availableSeats = 0;
        BigDecimal soldBoxOffice = BigDecimal.ZERO;
        BigDecimal lockedAmount = BigDecimal.ZERO;
        BigDecimal potentialBoxOffice = BigDecimal.ZERO;

        Map<Long, List<SeatInventory>> seatsByShow = new HashMap<>();
        Map<Long, List<SeatInventory>> seatsBySession = new HashMap<>();
        Map<String, List<SeatInventory>> seatsByZone = new LinkedHashMap<>();
        seatsByZone.put("VIP", new ArrayList<>());
        seatsByZone.put("STAGE_SIDE", new ArrayList<>());
        seatsByZone.put("STANDARD", new ArrayList<>());

        for (SeatInventory seat : seats) {
            BigDecimal price = seat.getPrice() != null ? seat.getPrice() : BigDecimal.ZERO;
            potentialBoxOffice = potentialBoxOffice.add(price);
            if ("SOLD".equals(seat.getStatus())) {
                soldSeats++;
                soldBoxOffice = soldBoxOffice.add(price);
            } else if ("LOCKED".equals(seat.getStatus())) {
                lockedSeats++;
                lockedAmount = lockedAmount.add(price);
            } else {
                availableSeats++;
            }
            seatsByShow.computeIfAbsent(seat.getShowId(), k -> new ArrayList<>()).add(seat);
            seatsBySession.computeIfAbsent(seat.getSessionId(), k -> new ArrayList<>()).add(seat);
            seatsByZone.computeIfAbsent(seat.getZoneCode(), k -> new ArrayList<>()).add(seat);
        }

        Map<Long, Integer> sessionCountByShow = new HashMap<>();
        for (ShowSession ss : sessions) {
            sessionCountByShow.merge(ss.getShowId(), 1, Integer::sum);
        }

        List<Map<String, Object>> showRanking = new ArrayList<>();
        for (ShowInfo show : shows) {
            List<SeatInventory> ssList = seatsByShow.getOrDefault(show.getId(), Collections.emptyList());
            int sTotal = ssList.size();
            int sSold = 0;
            int sLocked = 0;
            int sAvail = 0;
            BigDecimal sSoldAmt = BigDecimal.ZERO;
            BigDecimal sLockedAmt = BigDecimal.ZERO;
            BigDecimal sPotentialAmt = BigDecimal.ZERO;
            for (SeatInventory st : ssList) {
                BigDecimal p = st.getPrice() != null ? st.getPrice() : BigDecimal.ZERO;
                sPotentialAmt = sPotentialAmt.add(p);
                if ("SOLD".equals(st.getStatus())) {
                    sSold++;
                    sSoldAmt = sSoldAmt.add(p);
                } else if ("LOCKED".equals(st.getStatus())) {
                    sLocked++;
                    sLockedAmt = sLockedAmt.add(p);
                } else {
                    sAvail++;
                }
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("showId", show.getId());
            item.put("showCode", show.getShowCode());
            item.put("title", show.getTitle());
            item.put("category", show.getCategory());
            item.put("city", show.getCity());
            item.put("venueName", show.getVenueName());
            item.put("rating", show.getRating());
            item.put("hotScore", show.getHotScore());
            item.put("sessionCount", sessionCountByShow.getOrDefault(show.getId(), 0));
            item.put("totalSeats", sTotal);
            item.put("soldSeats", sSold);
            item.put("lockedSeats", sLocked);
            item.put("availableSeats", sAvail);
            item.put("occupancyRate", pct(sSold, sTotal));
            item.put("soldBoxOffice", sSoldAmt);
            item.put("lockedAmount", sLockedAmt);
            item.put("potentialBoxOffice", sPotentialAmt);
            showRanking.add(item);
        }
        showRanking.sort((a, b) -> ((BigDecimal) b.get("soldBoxOffice")).compareTo((BigDecimal) a.get("soldBoxOffice")));

        List<Map<String, Object>> sessionStats = new ArrayList<>();
        for (ShowSession ss : sessions) {
            List<SeatInventory> ssSeats = seatsBySession.getOrDefault(ss.getId(), Collections.emptyList());
            int sTotal = ssSeats.size();
            int sSold = 0;
            int sLocked = 0;
            int sAvail = 0;
            BigDecimal sSoldAmt = BigDecimal.ZERO;
            BigDecimal sPotentialAmt = BigDecimal.ZERO;
            for (SeatInventory st : ssSeats) {
                BigDecimal p = st.getPrice() != null ? st.getPrice() : BigDecimal.ZERO;
                sPotentialAmt = sPotentialAmt.add(p);
                if ("SOLD".equals(st.getStatus())) {
                    sSold++;
                    sSoldAmt = sSoldAmt.add(p);
                } else if ("LOCKED".equals(st.getStatus())) {
                    sLocked++;
                } else {
                    sAvail++;
                }
            }
            ShowInfo parent = showMap.get(ss.getShowId());
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("sessionId", ss.getId());
            item.put("sessionCode", ss.getSessionCode());
            item.put("showId", ss.getShowId());
            item.put("showTitle", parent != null ? parent.getTitle() : ("演出 #" + ss.getShowId()));
            item.put("city", parent != null ? parent.getCity() : "");
            item.put("sessionTitle", ss.getSessionTitle());
            item.put("hallName", ss.getHallName());
            item.put("startTime", ss.getStartTime() != null ? ss.getStartTime().format(DT_FMT) : null);
            item.put("totalSeats", sTotal);
            item.put("soldSeats", sSold);
            item.put("lockedSeats", sLocked);
            item.put("availableSeats", sAvail);
            item.put("occupancyRate", pct(sSold, sTotal));
            item.put("soldBoxOffice", sSoldAmt);
            item.put("potentialBoxOffice", sPotentialAmt);
            item.put("version", ss.getVersion());
            sessionStats.add(item);
        }

        Map<String, String> defaultZoneNames = Map.of(
                "VIP", "内场至尊VIP区",
                "STAGE_SIDE", "中轴声场优选区",
                "STANDARD", "全景看台标准区"
        );
        List<Map<String, Object>> zoneStats = new ArrayList<>();
        for (Map.Entry<String, List<SeatInventory>> entry : seatsByZone.entrySet()) {
            String zCode = entry.getKey();
            List<SeatInventory> zList = entry.getValue();
            int zTotal = zList.size();
            int zSold = 0;
            int zLocked = 0;
            int zAvail = 0;
            BigDecimal zSoldRev = BigDecimal.ZERO;
            BigDecimal zPotentialRev = BigDecimal.ZERO;
            for (SeatInventory st : zList) {
                BigDecimal p = st.getPrice() != null ? st.getPrice() : BigDecimal.ZERO;
                zPotentialRev = zPotentialRev.add(p);
                if ("SOLD".equals(st.getStatus())) {
                    zSold++;
                    zSoldRev = zSoldRev.add(p);
                } else if ("LOCKED".equals(st.getStatus())) {
                    zLocked++;
                } else {
                    zAvail++;
                }
            }
            Map<String, Object> zItem = new LinkedHashMap<>();
            zItem.put("zoneCode", zCode);
            zItem.put("zoneName", !zList.isEmpty() ? zList.get(0).getZoneName() : defaultZoneNames.getOrDefault(zCode, zCode));
            zItem.put("totalSeats", zTotal);
            zItem.put("soldSeats", zSold);
            zItem.put("lockedSeats", zLocked);
            zItem.put("availableSeats", zAvail);
            zItem.put("sellThroughRate", pct(zSold, zTotal));
            zItem.put("soldRevenue", zSoldRev);
            zItem.put("potentialRevenue", zPotentialRev);
            zoneStats.add(zItem);
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("totalShows", shows.size());
        summary.put("totalSessions", sessions.size());
        summary.put("totalSeats", totalSeats);
        summary.put("soldSeats", soldSeats);
        summary.put("lockedSeats", lockedSeats);
        summary.put("availableSeats", availableSeats);
        summary.put("occupancyRate", pct(soldSeats, totalSeats));
        summary.put("soldBoxOffice", soldBoxOffice);
        summary.put("lockedAmount", lockedAmount);
        summary.put("potentialBoxOffice", potentialBoxOffice);

        Map<String, Object> res = new LinkedHashMap<>();
        res.put("summary", summary);
        res.put("showRanking", showRanking);
        res.put("sessionStats", sessionStats);
        res.put("zoneStats", zoneStats);
        return res;
    }

    /**
     * ADMIN 专属：读取 ticket_show 库全表元数据、实时座位库存与 Redis 缓存键状态
     */
    public Map<String, Object> getAdminDbOverview() {
        List<ShowInfo> shows = showInfoMapper.selectList(
                new LambdaQueryWrapper<ShowInfo>().orderByAsc(ShowInfo::getId)
        );
        List<ShowSession> sessions = showSessionMapper.selectList(
                new LambdaQueryWrapper<ShowSession>().orderByAsc(ShowSession::getId)
        );
        List<SeatInventory> seats = seatInventoryMapper.selectList(
                new LambdaQueryWrapper<SeatInventory>()
                        .orderByAsc(SeatInventory::getSessionId)
                        .orderByAsc(SeatInventory::getRowNo)
                        .orderByAsc(SeatInventory::getColNo)
        );

        List<Map<String, Object>> undoLogs = jdbcTemplate.queryForList(
                """
                SELECT branch_id AS branchId, xid, context, log_status AS logStatus,
                       OCTET_LENGTH(rollback_info) AS payloadBytes,
                       DATE_FORMAT(log_created, '%Y-%m-%d %H:%i:%s') AS logCreated
                FROM undo_log
                ORDER BY log_created DESC
                LIMIT 50
                """
        );

        List<Map<String, Object>> redisCacheKeys = new ArrayList<>();
        List<Long> inspectIds = new ArrayList<>();
        for (ShowInfo s : shows) {
            inspectIds.add(s.getId());
        }
        inspectIds.add(99999L);
        for (Long id : inspectIds) {
            String key = CACHE_SHOW_DETAIL_PREFIX + id;
            String val = redisTemplate.opsForValue().get(key);
            Long ttl = redisTemplate.getExpire(key, TimeUnit.SECONDS);
            Map<String, Object> kInfo = new LinkedHashMap<>();
            kInfo.put("key", key);
            kInfo.put("showId", id);
            kInfo.put("exists", val != null);
            kInfo.put("ttlSeconds", ttl != null && ttl > 0 ? ttl : 0L);
            if (val == null) {
                kInfo.put("cacheType", "MISS");
                kInfo.put("payloadBytes", 0);
            } else if (NULL_SENTINEL.equals(val)) {
                kInfo.put("cacheType", "NULL_SENTINEL");
                kInfo.put("payloadBytes", val.length());
            } else {
                kInfo.put("cacheType", "DATA_JSON");
                kInfo.put("payloadBytes", val.length());
            }
            redisCacheKeys.add(kInfo);
        }

        List<Map<String, Object>> tablesMeta = queryTablesMeta(List.of(
                "show_info", "show_session", "seat_inventory", "undo_log"
        ));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("database", "ticket_show");
        data.put("service", "show-service (:8202)");
        data.put("tablesMeta", tablesMeta);
        data.put("redisCacheKeys", redisCacheKeys);
        data.put("shows", shows);
        data.put("sessions", sessions);
        data.put("seatInventory", seats);
        data.put("undoLogs", undoLogs);
        return data;
    }

    private List<Map<String, Object>> queryTablesMeta(List<String> tableNames) {
        Map<String, Map<String, Object>> schemaInfo = new LinkedHashMap<>();
        List<Map<String, Object>> rawMeta = jdbcTemplate.queryForList(
                """
                SELECT TABLE_NAME AS tableName, TABLE_COMMENT AS tableComment, ENGINE AS engine,
                       IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0) AS totalBytes
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                """
        );
        for (Map<String, Object> m : rawMeta) {
            schemaInfo.put(String.valueOf(m.get("tableName")), m);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (String tbl : tableNames) {
            Long exactCount = jdbcTemplate.queryForObject("SELECT COUNT(*) FROM " + tbl, Long.class);
            Map<String, Object> info = schemaInfo.getOrDefault(tbl, Map.of());
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("tableName", tbl);
            item.put("tableComment", info.getOrDefault("tableComment", ""));
            item.put("engine", info.getOrDefault("engine", "InnoDB"));
            item.put("rowCount", exactCount != null ? exactCount : 0L);
            item.put("totalBytes", info.getOrDefault("totalBytes", 0L));
            result.add(item);
        }
        return result;
    }

    private BigDecimal pct(int part, int whole) {
        if (whole <= 0) {
            return BigDecimal.ZERO;
        }
        return BigDecimal.valueOf(part * 100.0 / whole).setScale(1, RoundingMode.HALF_UP);
    }

    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> saveOrUpdateShow(Map<String, Object> payload) {
        Long id = payload.get("id") != null && !String.valueOf(payload.get("id")).isBlank()
                ? Long.valueOf(String.valueOf(payload.get("id")))
                : null;
        String title = strVal(payload.get("title"), "").trim();
        if (title.isEmpty()) {
            throw new BizException(400, "演出名称不能为空");
        }

        boolean isNew = (id == null || id <= 0);
        ShowInfo show;
        if (isNew) {
            show = new ShowInfo();
            show.setShowCode("PT-" + System.currentTimeMillis() % 1000000);
            show.setCreatedAt(java.time.LocalDateTime.now());
        } else {
            show = showInfoMapper.selectById(id);
            if (show == null) {
                throw new BizException(404, "未找到对应的演出记录 #" + id);
            }
        }

        show.setTitle(title);
        show.setSubtitle(strVal(payload.get("subtitle"), "官方正版授权 · 沉浸式声学现场"));
        show.setCategory(strVal(payload.get("category"), "CONCERT").toUpperCase());
        show.setCity(strVal(payload.get("city"), "上海"));
        show.setVenueName(strVal(payload.get("venueName"), "梅赛德斯-奔驰文化中心 · 主场馆"));
        if (payload.containsKey("posterUrl")) {
            show.setPosterUrl(strVal(payload.get("posterUrl"), null));
        }
        show.setPosterGradient(strVal(payload.get("posterGradient"), "from-indigo-600 via-violet-600 to-fuchsia-600"));
        show.setBadgeText(strVal(payload.get("badgeText"), "官方直售 · 电子票"));
        show.setRating(decVal(payload.get("rating"), new BigDecimal("9.8")));

        BigDecimal minPrice = decVal(payload.get("minPrice"), new BigDecimal("280.00"));
        BigDecimal maxPrice = decVal(payload.get("maxPrice"), new BigDecimal("880.00"));
        if (maxPrice.compareTo(minPrice) < 0) {
            maxPrice = minPrice;
        }
        show.setMinPrice(minPrice);
        show.setMaxPrice(maxPrice);
        show.setTags(strVal(payload.get("tags"), "阿里云OSS海报,支持在线选座,秒杀防重保护"));
        show.setDescription(strVal(payload.get("description"), title + " — 官方电子票在线选座。"));
        show.setHotScore(intVal(payload.get("hotScore"), 9600));
        show.setStatus(strVal(payload.get("status"), "ON_SALE"));

        if (isNew) {
            showInfoMapper.insert(show);
        } else {
            showInfoMapper.updateById(show);
        }

        LocalDateTime customStartTime = parseFlexibleDateTime(strVal(payload.get("startTime"), null));
        if (customStartTime != null && customStartTime.isBefore(LocalDateTime.now())) {
            throw new BizException(400, "场次开演时间必须晚于当前时间");
        }
        LocalDateTime effectiveStart = customStartTime != null
                ? customStartTime
                : LocalDateTime.now().plusDays(14).withHour(19).withMinute(30).withSecond(0).withNano(0);

        String rawSessionTitle = strVal(payload.get("sessionTitle"), "").trim();
        String hallName = strVal(payload.get("hallName"), "中央全息沉浸演艺厅");
        ShowSession createdSession = null;
        if (isNew || !rawSessionTitle.isEmpty() || customStartTime != null) {
            String effectiveTitle = buildSessionTitleWithSchedule(
                    rawSessionTitle.isEmpty() ? "首演黄金场" : rawSessionTitle,
                    effectiveStart
            );
            createdSession = createSessionWithSeats(show, effectiveTitle, hallName, effectiveStart);
        }

        try {
            jdbcTemplate.update(
                    "INSERT IGNORE INTO venue_dict (city, venue_name, hall_name, category_hint, sort_order) VALUES (?, ?, ?, ?, 80)",
                    show.getCity(), show.getVenueName(), hallName, show.getCategory()
            );
        } catch (Exception ignored) {
        }

        evictShowCaches(show.getId(), createdSession != null ? createdSession.getId() : null);

        List<ShowSession> sessions = showSessionMapper.selectList(
                new LambdaQueryWrapper<ShowSession>()
                        .eq(ShowSession::getShowId, show.getId())
                        .orderByAsc(ShowSession::getStartTime)
        );
        Map<String, Object> res = new LinkedHashMap<>();
        res.put("show", show);
        res.put("sessions", sessions);
        res.put("isNew", isNew);
        return res;
    }

    @Transactional(rollbackFor = Exception.class)
    public ShowInfo updateShowStatus(Long showId, String status) {
        ShowInfo show = showInfoMapper.selectById(showId);
        if (show == null) {
            throw new BizException(404, "演出不存在 #" + showId);
        }
        String normalized = "OFF_SHELF".equalsIgnoreCase(status) ? "OFF_SHELF" : "ON_SALE";
        show.setStatus(normalized);
        showInfoMapper.updateById(show);
        evictShowCaches(showId, null);
        return show;
    }

    @Transactional(rollbackFor = Exception.class)
    public void deleteShow(Long showId) {
        ShowInfo show = showInfoMapper.selectById(showId);
        if (show == null) {
            throw new BizException(404, "演出不存在 #" + showId);
        }
        Long occupiedCount = seatInventoryMapper.selectCount(
                new LambdaQueryWrapper<SeatInventory>()
                        .eq(SeatInventory::getShowId, showId)
                        .ne(SeatInventory::getStatus, "AVAILABLE")
        );
        if (occupiedCount != null && occupiedCount > 0) {
            throw new BizException(409, "该演出已有 " + occupiedCount + " 个已售或锁定席位，为保护历史订单审计请使用「下架」功能");
        }
        seatInventoryMapper.delete(new LambdaQueryWrapper<SeatInventory>().eq(SeatInventory::getShowId, showId));
        showSessionMapper.delete(new LambdaQueryWrapper<ShowSession>().eq(ShowSession::getShowId, showId));
        showInfoMapper.deleteById(showId);
        evictShowCaches(showId, null);
    }

    /**
     * 从数据库 venue_dict、show_info、show_session 实时聚合下拉选项数据
     */
    public Map<String, Object> getAdminFormOptions() {
        List<Map<String, Object>> venues = new ArrayList<>();
        try {
            venues.addAll(jdbcTemplate.queryForList(
                    """
                    SELECT city, venue_name AS venueName, hall_name AS hallName, category_hint AS categoryHint
                    FROM venue_dict
                    ORDER BY sort_order ASC, id ASC
                    """
            ));
        } catch (Exception ignored) {
        }

        // 兜底合并 show_info 与 show_session 中的全部真实城市、场馆与演艺厅
        List<ShowInfo> shows = showInfoMapper.selectList(
                new LambdaQueryWrapper<ShowInfo>().orderByDesc(ShowInfo::getHotScore).orderByDesc(ShowInfo::getId)
        );
        List<ShowSession> sessions = showSessionMapper.selectList(
                new LambdaQueryWrapper<ShowSession>().orderByAsc(ShowSession::getId)
        );
        Map<Long, String> firstHallByShow = new HashMap<>();
        Set<String> hallSet = new LinkedHashSet<>();
        for (ShowSession s : sessions) {
            if (s.getHallName() != null && !s.getHallName().isBlank()) {
                firstHallByShow.putIfAbsent(s.getShowId(), s.getHallName());
                hallSet.add(s.getHallName());
            }
        }

        Set<String> seenCityVenue = new HashSet<>();
        for (Map<String, Object> v : venues) {
            String c = String.valueOf(v.getOrDefault("city", ""));
            String vn = String.valueOf(v.getOrDefault("venueName", ""));
            String hn = String.valueOf(v.getOrDefault("hallName", ""));
            seenCityVenue.add(c + "::" + vn);
            if (!hn.isBlank()) {
                hallSet.add(hn);
            }
        }

        Set<String> badgeSet = new LinkedHashSet<>();
        for (ShowInfo s : shows) {
            if (s.getCity() != null && s.getVenueName() != null) {
                String key = s.getCity() + "::" + s.getVenueName();
                if (seenCityVenue.add(key)) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("city", s.getCity());
                    item.put("venueName", s.getVenueName());
                    item.put("hallName", firstHallByShow.getOrDefault(s.getId(), "中央全息环形演艺厅"));
                    item.put("categoryHint", s.getCategory());
                    venues.add(item);
                }
            }
            if (s.getBadgeText() != null && !s.getBadgeText().isBlank()) {
                badgeSet.add(s.getBadgeText());
            }
        }

        badgeSet.addAll(List.of(
                "年度必看爆款",
                "官方直售 · 电子票",
                "全球巡演驻场原版",
                "1.43:1 满画幅顶配",
                "户外草坪微醺现场",
                "限时返场加演"
        ));

        Set<String> citySet = new LinkedHashSet<>();
        for (Map<String, Object> v : venues) {
            String c = String.valueOf(v.getOrDefault("city", ""));
            if (!c.isBlank()) {
                citySet.add(c);
            }
        }

        Map<String, Object> res = new LinkedHashMap<>();
        res.put("cities", new ArrayList<>(citySet));
        res.put("venues", venues);
        res.put("halls", new ArrayList<>(hallSet));
        res.put("badges", new ArrayList<>(badgeSet));
        return res;
    }

    /**
     * 按管理员指定的 ID 顺序批量重排演出展示顺位（持久化更新 hot_score 并驱逐缓存）
     */
    @Transactional(rollbackFor = Exception.class)
    public List<Map<String, Object>> reorderShows(List<Long> orderedIds) {
        if (orderedIds == null || orderedIds.isEmpty()) {
            return listAllShowsForAdmin(null);
        }
        int baseScore = 10000;
        int step = 100;
        for (int i = 0; i < orderedIds.size(); i++) {
            Long showId = orderedIds.get(i);
            if (showId == null) continue;
            int newScore = Math.max(100, baseScore - (i * step));
            jdbcTemplate.update("UPDATE show_info SET hot_score = ? WHERE id = ?", newScore, showId);
            evictShowCaches(showId, null);
        }
        return listAllShowsForAdmin(null);
    }

    private static final Pattern LEADING_CN_DATE_PATTERN = Pattern.compile(
            "^\\s*\\d{1,2}月\\d{1,2}日(?:\\s*周[一二三四五六日天])?(?:\\s*\\d{1,2}:\\d{2})?\\s*"
    );

    public static String chineseWeekday(DayOfWeek dow) {
        return switch (dow) {
            case MONDAY -> "周一";
            case TUESDAY -> "周二";
            case WEDNESDAY -> "周三";
            case THURSDAY -> "周四";
            case FRIDAY -> "周五";
            case SATURDAY -> "周六";
            case SUNDAY -> "周日";
        };
    }

    public static String formatSessionPrefix(LocalDateTime dt) {
        return String.format("%d月%d日 %s %02d:%02d",
                dt.getMonthValue(), dt.getDayOfMonth(), chineseWeekday(dt.getDayOfWeek()),
                dt.getHour(), dt.getMinute());
    }

    public static String buildSessionTitleWithSchedule(String rawTitle, LocalDateTime startTime) {
        String stripped = rawTitle != null ? LEADING_CN_DATE_PATTERN.matcher(rawTitle).replaceFirst("").trim() : "";
        if (stripped.isEmpty()) {
            stripped = "首演黄金场";
        }
        return formatSessionPrefix(startTime) + " " + stripped;
    }

    private LocalDateTime parseFlexibleDateTime(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        String clean = raw.trim().replace(' ', 'T');
        if (clean.length() > 19) {
            clean = clean.substring(0, 19);
        }
        List<DateTimeFormatter> formatters = List.of(
                DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"),
                DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm")
        );
        for (DateTimeFormatter fmt : formatters) {
            try {
                return LocalDateTime.parse(clean, fmt);
            } catch (Exception ignored) {
            }
        }
        throw new BizException(400, "开演时间格式无效，请使用日期选择器（如 2026-12-24T19:30）");
    }

    private ShowSession createSessionWithSeats(ShowInfo show, String sessionTitle, String hallName, LocalDateTime startTime) {
        LocalDateTime effectiveStart = startTime != null
                ? startTime
                : LocalDateTime.now().plusDays(14).withHour(19).withMinute(30).withSecond(0).withNano(0);
        ShowSession session = ShowSession.builder()
                .showId(show.getId())
                .sessionCode("SES-" + show.getId() + "-" + (System.currentTimeMillis() % 10000))
                .sessionTitle(sessionTitle)
                .hallName(hallName)
                .startTime(effectiveStart)
                .endTime(effectiveStart.plusHours(2).plusMinutes(30))
                .totalSeats(48)
                .availableSeats(48)
                .lockedSeats(0)
                .soldSeats(0)
                .version(0)
                .createdAt(LocalDateTime.now())
                .build();
        showSessionMapper.insert(session);

        BigDecimal minP = show.getMinPrice() != null ? show.getMinPrice() : new BigDecimal("280.00");
        BigDecimal maxP = show.getMaxPrice() != null ? show.getMaxPrice() : new BigDecimal("880.00");
        BigDecimal midP = minP.add(maxP).divide(new BigDecimal("2"), 0, RoundingMode.HALF_UP);

        for (int r = 1; r <= 6; r++) {
            String zoneCode;
            String zoneName;
            BigDecimal price;
            if (r <= 2) {
                zoneCode = "VIP";
                zoneName = "内场全息至尊区";
                price = maxP;
            } else if (r <= 4) {
                zoneCode = "STAGE_SIDE";
                zoneName = "中轴声场优选区";
                price = midP;
            } else {
                zoneCode = "STANDARD";
                zoneName = "全景看台标准区";
                price = minP;
            }
            for (int c = 1; c <= 8; c++) {
                SeatInventory seat = SeatInventory.builder()
                        .sessionId(session.getId())
                        .showId(show.getId())
                        .rowNo(r)
                        .colNo(c)
                        .seatLabel(String.format("%s %d排%02d座", zoneName, r, c))
                        .zoneCode(zoneCode)
                        .zoneName(zoneName)
                        .price(price)
                        .status("AVAILABLE")
                        .version(0)
                        .build();
                seatInventoryMapper.insert(seat);
            }
        }
        return session;
    }

    private String strVal(Object v, String def) {
        if (v == null) return def;
        String s = String.valueOf(v);
        return s.isBlank() ? def : s;
    }

    private BigDecimal decVal(Object v, BigDecimal def) {
        if (v == null) return def;
        try {
            return new BigDecimal(String.valueOf(v).trim());
        } catch (Exception e) {
            return def;
        }
    }

    private Integer intVal(Object v, Integer def) {
        if (v == null) return def;
        try {
            return Integer.valueOf(String.valueOf(v).trim());
        } catch (Exception e) {
            return def;
        }
    }

    private void registerPostTxCacheEviction(Long showId, Long sessionId) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCompletion(int status) {
                    evictShowCaches(showId, sessionId);
                }
            });
        }
    }
}
