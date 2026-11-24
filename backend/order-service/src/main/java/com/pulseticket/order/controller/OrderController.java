package com.pulseticket.order.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.pulseticket.common.context.UserContext;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.common.result.Result;
import com.pulseticket.order.entity.TicketOrder;
import com.pulseticket.order.mapper.TicketOrderMapper;
import com.pulseticket.order.service.OrderFacade;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 订单中心与交易控制 REST 入口（服务端口 :8203）。
 *
 * 【同时服务两端流量】：
 * 1. Vue 前端（经 ticket-gateway 验签 JWT 后透传 UserContext）
 * 2. Python LangGraph Agent（用户点选确定性确认卡片后，携 Idempotency-Key 调用）
 * 所有创单、支付、取消、退票及座位释放均以本服务 Java 强一致性状态机与 Seata AT 事务为准。
 */
@RestController
@RequestMapping("/api/order")
@RequiredArgsConstructor
public class OrderController {

    private final OrderFacade orderFacade;
    private final TicketOrderMapper ticketOrderMapper;
    private static final DateTimeFormatter DT_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    @Data
    public static class CreateOrderRequest {
        private Long sessionId;
        private List<Long> seatIds;
        private Boolean fastCloseDemo;
        private Boolean simulateSeataRollback;
        private Integer stressSlot;
        private String idempotencyKey;
    }

    /**
     * 公开电子票闸机扫码核验接口（微信扫一扫 / 手机相机直接打开 H5 核验页调用）
     */
    @GetMapping("/public/verify/{ticketCode}")
    public Result<Map<String, Object>> verifyTicketPublic(@PathVariable String ticketCode) {
        String code = ticketCode != null ? ticketCode.trim().toUpperCase() : "";
        if (code.isEmpty()) {
            throw new BizException(400, "核验码不能为空");
        }
        TicketOrder order = ticketOrderMapper.selectOne(
                new LambdaQueryWrapper<TicketOrder>().eq(TicketOrder::getTicketCode, code)
        );
        if (order == null) {
            throw new BizException(404, "未找到该电子票核验码，请确认票面真伪");
        }

        Map<String, Object> vo = new LinkedHashMap<>();
        vo.put("valid", "PAID".equals(order.getStatus()));
        vo.put("status", order.getStatus());
        vo.put("ticketCode", order.getTicketCode());
        vo.put("orderNo", order.getOrderNo());
        vo.put("showId", order.getShowId());
        vo.put("showTitle", order.getShowTitle());
        vo.put("venueName", order.getVenueName());
        vo.put("sessionTitle", order.getSessionTitle());
        vo.put("seatLabelsJson", order.getSeatLabelsJson());
        vo.put("seatCount", order.getSeatCount());
        vo.put("totalAmount", order.getTotalAmount());
        vo.put("paidAt", order.getPaidAt() != null ? order.getPaidAt().format(DT_FMT) : null);
        vo.put("verifiedAt", LocalDateTime.now().format(DT_FMT));
        return Result.ok("电子票核验成功", vo);
    }

    /**
     * 本地开发辅助：返回宿主机局域网 IPv4 地址，使 localhost 下生成的二维码也可被同 Wi-Fi 手机微信直接扫码打开
     */
    @GetMapping("/public/lan-ip")
    public Result<Map<String, String>> getLanIp() {
        String ip = detectLanIpv4();
        return Result.ok(Map.of("lanIp", ip));
    }

    /**
     * 选座锁座并创建待支付订单：
     * - 支持 Idempotency-Key 幂等防重（Python Agent 确认卡片点击或前端网络重试都不会重复建单）；
     * - 只有管理员才能开启“15秒极速超时演示”、“Seata 故障回滚注入”和“虚拟买家并发压测”。
     */
    @PostMapping("/create")
    public Result<TicketOrder> createOrder(
            @RequestBody CreateOrderRequest req,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyHeader,
            @RequestHeader(value = "X-Idempotency-Key", required = false) String xIdempotencyHeader
    ) {
        Long userId = requireUserId();
        String username = UserContext.getUsername() != null ? UserContext.getUsername() : "user-" + userId;

        boolean isAdmin = UserContext.isAdmin();
        // 普通购票用户强制使用标准商业模式（15分钟锁座、禁止故障注入）
        boolean fastClose = isAdmin && Boolean.TRUE.equals(req.getFastCloseDemo());
        boolean simulateRollback = isAdmin && Boolean.TRUE.equals(req.getSimulateSeataRollback());

        String idempotencyKey = firstNonBlank(idempotencyHeader, xIdempotencyHeader, req.getIdempotencyKey());

        // 架构师并发压测虚拟买家槽位（模拟 20 个不同买家并发争抢同一座位）
        if (isAdmin && req.getStressSlot() != null && req.getStressSlot() > 0 && req.getStressSlot() <= 100) {
            userId = 9000L + req.getStressSlot();
            username = "sim_buyer_" + req.getStressSlot();
            idempotencyKey = null;
        }

        TicketOrder order = orderFacade.createOrderWithThreeLayerDefense(
                userId,
                username,
                req.getSessionId(),
                req.getSeatIds(),
                fastClose,
                simulateRollback,
                idempotencyKey
        );
        return Result.ok("锁座下单成功，请在倒计时结束前完成支付", order);
    }

    /** 模拟收银台支付订单：CAS 将 UNPAID 改为 PAID，签发电子票码并通过 MQ 广播出票与发积分。 */
    @PostMapping("/{orderNo}/pay")
    public Result<TicketOrder> payOrder(@PathVariable String orderNo) {
        Long userId = requireUserId();
        TicketOrder paid = orderFacade.payOrder(userId, orderNo);
        return Result.ok("支付成功！电子票已签发", paid);
    }

    /** 主动取消未支付订单：CAS 关闭订单并立即通知 show-service 归还锁定座位。 */
    @PostMapping("/{orderNo}/cancel")
    public Result<TicketOrder> cancelOrder(@PathVariable String orderNo) {
        Long userId = requireUserId();
        TicketOrder cancelled = orderFacade.cancelOrderManually(userId, orderNo);
        return Result.ok("订单已取消，锁定座位已释放", cancelled);
    }

    /** 已支付订单自助阶梯退票：按距开演时间算手续费，释放座位并广播 MQ 回退观演积分。 */
    @PostMapping("/{orderNo}/refund")
    public Result<Map<String, Object>> refundOrder(@PathVariable String orderNo) {
        Long userId = requireUserId();
        Map<String, Object> receipt = orderFacade.refundPaidOrder(userId, orderNo);
        return Result.ok("退票申请已办结，座位已释放", receipt);
    }

    /** 查询当前登录用户自己的全部历史订单（按创建时间倒序）。 */
    @GetMapping("/my")
    public Result<List<TicketOrder>> listMyOrders() {
        Long userId = requireUserId();
        return Result.ok(orderFacade.listUserOrders(userId));
    }

    /** 按订单号查询单笔订单详情（严格校验订单归属当前登录用户）。 */
    @GetMapping("/detail/{orderNo}")
    public Result<TicketOrder> getOrderDetail(@PathVariable String orderNo) {
        Long userId = requireUserId();
        return Result.ok(orderFacade.getOrderByNo(userId, orderNo));
    }

    private Long requireUserId() {
        Long userId = UserContext.getUserId();
        if (userId == null) {
            throw new BizException(401, "请先登录账号");
        }
        return userId;
    }

    private String firstNonBlank(String... values) {
        if (values == null) {
            return null;
        }
        for (String v : values) {
            if (v != null && !v.trim().isEmpty()) {
                return v.trim();
            }
        }
        return null;
    }

    @GetMapping("/telemetry")
    public Result<Map<String, Object>> getTelemetry() {
        if (!UserContext.isAdmin()) {
            throw new BizException(403, "仅限架构运维专员查看底层事务与消息审计日志");
        }
        return Result.ok(orderFacade.getArchitectureTelemetry());
    }

    @GetMapping("/admin/sales")
    public Result<Map<String, Object>> getAdminSales() {
        if (!UserContext.isAdmin()) {
            throw new BizException(403, "仅限架构运维专员查看全平台票房销量面板");
        }
        return Result.ok(orderFacade.getAdminOrderSales());
    }

    @GetMapping("/admin/db-overview")
    public Result<Map<String, Object>> getAdminDbOverview() {
        if (!UserContext.isAdmin()) {
            throw new BizException(403, "仅限架构运维专员查看订单与事务审计数据库");
        }
        return Result.ok(orderFacade.getAdminDbOverview());
    }

    private String detectLanIpv4() {
        String fallback = "127.0.0.1";
        try {
            Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                NetworkInterface ni = interfaces.nextElement();
                if (!ni.isUp() || ni.isLoopback() || ni.isVirtual()) continue;
                String name = ni.getDisplayName().toLowerCase();
                if (name.contains("docker") || name.contains("wsl") || name.contains("vethernet") || name.contains("vmware") || name.contains("virtualbox")) {
                    continue;
                }
                Enumeration<InetAddress> addrs = ni.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress addr = addrs.nextElement();
                    if (addr instanceof Inet4Address && !addr.isLoopbackAddress() && addr.isSiteLocalAddress()) {
                        String host = addr.getHostAddress();
                        if (host.startsWith("192.168.") || host.startsWith("10.")) {
                            return host;
                        }
                        fallback = host;
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return fallback;
    }
}
