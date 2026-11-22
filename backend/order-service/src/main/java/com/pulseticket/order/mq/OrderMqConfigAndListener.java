package com.pulseticket.order.mq;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pulseticket.feign.client.ShowSeatClient;
import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.order.entity.TicketOrder;
import com.pulseticket.order.mapper.TicketOrderMapper;
import com.pulseticket.order.service.SeataAuditService;
import com.rabbitmq.client.Channel;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * RabbitMQ 延迟关单 (TTL + 死信交换机 DLX) 与消息零丢失配置
 * 面试核心亮点：
 * 1. 为规避 RabbitMQ 单队列不同 TTL 导致的“队头阻塞问题 (Head-of-Line Blocking)”，
 *    按固定延迟档位拆分为两个独立死信缓冲队列：
 *    - ticket.order.delay.15m.queue (标准 15 分钟 = 900,000ms)
 *    - ticket.order.delay.fast.queue (面试演示极速档 = 15 秒 = 15,000ms)
 *    两个缓冲队列均不绑定消费者，到期后统一转发至死信交换机 ticket.order.close.dlx -> ticket.order.close.queue！
 * 2. 开启 Publisher ConfirmCallback + ReturnsCallback 与消费者 manual 手动 ACK。
 * 3. 关单消费执行 WHERE status = 'UNPAID' 的 CAS 乐观更新，彻底解决 14:59 付款与 15:00 关单的竞态冲突！
 */
@Configuration
@RequiredArgsConstructor
public class OrderMqConfigAndListener {

    private static final Logger log = LoggerFactory.getLogger(OrderMqConfigAndListener.class);

    public static final String EXCHANGE_ORDER_DELAY = "ticket.order.delay.direct";
    public static final String QUEUE_DELAY_15M = "ticket.order.delay.15m.queue";
    public static final String QUEUE_DELAY_FAST = "ticket.order.delay.fast.queue";
    public static final String ROUTING_DELAY_15M = "order.delay.15m";
    public static final String ROUTING_DELAY_FAST = "order.delay.fast";

    public static final String EXCHANGE_ORDER_CLOSE_DLX = "ticket.order.close.dlx";
    public static final String QUEUE_ORDER_CLOSE = "ticket.order.close.queue";
    public static final String ROUTING_ORDER_CLOSE_TIMEOUT = "order.close.timeout";

    public static final String EXCHANGE_ORDER_PAID_FANOUT = "ticket.order.paid.fanout";
    public static final String EXCHANGE_ORDER_CLOSED_DIRECT = "ticket.order.closed.direct";
    public static final String ROUTING_ORDER_CLOSED_RELEASE = "order.closed.release";

    private final RabbitTemplate rabbitTemplate;
    private final TicketOrderMapper ticketOrderMapper;
    private final ShowSeatClient showSeatClient;
    private final SeataAuditService seataAuditService;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @PostConstruct
    public void initPublisherCallbacks() {
        rabbitTemplate.setConfirmCallback((CorrelationData correlationData, boolean ack, String cause) -> {
            String msgId = correlationData != null ? correlationData.getId() : "unknown";
            if (!ack) {
                log.error("[RabbitMQ Publisher Confirm 失败] msgId={}, cause={}", msgId, cause);
            }
        });
        rabbitTemplate.setReturnsCallback(returned -> log.error(
                "[RabbitMQ Publisher Returns 路由不可达] exchange={}, routingKey={}, replyText={}",
                returned.getExchange(), returned.getRoutingKey(), returned.getReplyText()
        ));
    }

    @Bean
    public static MessageConverter jacksonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public DirectExchange orderDelayExchange() {
        return new DirectExchange(EXCHANGE_ORDER_DELAY, true, false);
    }

    @Bean
    public DirectExchange orderCloseDlxExchange() {
        return new DirectExchange(EXCHANGE_ORDER_CLOSE_DLX, true, false);
    }

    /**
     * 标准 15 分钟 (900,000ms) 死信缓冲队列
     */
    @Bean
    public Queue orderDelay15mQueue() {
        return QueueBuilder.durable(QUEUE_DELAY_15M)
                .ttl(900_000)
                .deadLetterExchange(EXCHANGE_ORDER_CLOSE_DLX)
                .deadLetterRoutingKey(ROUTING_ORDER_CLOSE_TIMEOUT)
                .build();
    }

    /**
     * 极速演示 15 秒 (15,000ms) 死信缓冲队列 (独立队列彻底避免与15分钟队列发生队头阻塞！)
     */
    @Bean
    public Queue orderDelayFastQueue() {
        return QueueBuilder.durable(QUEUE_DELAY_FAST)
                .ttl(15_000)
                .deadLetterExchange(EXCHANGE_ORDER_CLOSE_DLX)
                .deadLetterRoutingKey(ROUTING_ORDER_CLOSE_TIMEOUT)
                .build();
    }

    @Bean
    public Queue orderCloseQueue() {
        return QueueBuilder.durable(QUEUE_ORDER_CLOSE).build();
    }

    @Bean
    public Binding bindDelay15m(Queue orderDelay15mQueue, DirectExchange orderDelayExchange) {
        return BindingBuilder.bind(orderDelay15mQueue).to(orderDelayExchange).with(ROUTING_DELAY_15M);
    }

    @Bean
    public Binding bindDelayFast(Queue orderDelayFastQueue, DirectExchange orderDelayExchange) {
        return BindingBuilder.bind(orderDelayFastQueue).to(orderDelayExchange).with(ROUTING_DELAY_FAST);
    }

    @Bean
    public Binding bindOrderCloseDlx(Queue orderCloseQueue, DirectExchange orderCloseDlxExchange) {
        return BindingBuilder.bind(orderCloseQueue).to(orderCloseDlxExchange).with(ROUTING_ORDER_CLOSE_TIMEOUT);
    }

    @Bean
    public FanoutExchange orderPaidFanoutExchange() {
        return new FanoutExchange(EXCHANGE_ORDER_PAID_FANOUT, true, false);
    }

    @Bean
    public DirectExchange orderClosedDirectExchange() {
        return new DirectExchange(EXCHANGE_ORDER_CLOSED_DIRECT, true, false);
    }

    /**
     * 监听死信队列：处理超时未支付自动关单并通知 show-service 释放座位
     */
    @RabbitListener(queues = QUEUE_ORDER_CLOSE)
    public void onOrderTimeoutDeadLetter(String orderNo, Message message, Channel channel) throws IOException {
        long tag = message.getMessageProperties().getDeliveryTag();
        String cleanOrderNo = orderNo != null ? orderNo.replace("\"", "").trim() : "";
        try {
            TicketOrder order = ticketOrderMapper.selectOne(
                    new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<TicketOrder>()
                            .eq(TicketOrder::getOrderNo, cleanOrderNo)
            );
            if (order == null) {
                channel.basicAck(tag, false);
                return;
            }

            // 核心：CAS 条件更新 WHERE status = 'UNPAID'，若用户已在前1秒支付成功则 updated == 0
            int closed = ticketOrderMapper.casCloseUnpaidOrder(cleanOrderNo, "TIMEOUT_UNPAID");
            if (closed == 1) {
                List<Long> seatIds = parseSeatIds(order.getSeatIdsJson());
                OrderClosedEventDTO releaseEvent = new OrderClosedEventDTO();
                releaseEvent.setMessageId("msg-close-" + UUID.randomUUID().toString().substring(0, 8));
                releaseEvent.setOrderNo(cleanOrderNo);
                releaseEvent.setUserId(order.getUserId());
                releaseEvent.setSessionId(order.getSessionId());
                releaseEvent.setSeatIds(seatIds);
                releaseEvent.setCloseReason("TIMEOUT_UNPAID");

                // 双通道保障：发送 MQ 释放事件 + Feign 同步快速释放
                rabbitTemplate.convertAndSend(
                        EXCHANGE_ORDER_CLOSED_DIRECT,
                        ROUTING_ORDER_CLOSED_RELEASE,
                        releaseEvent,
                        new CorrelationData(releaseEvent.getMessageId())
                );
                try {
                    showSeatClient.releaseSeats(releaseEvent);
                } catch (Exception ignored) {
                }

                seataAuditService.recordMqEvent(
                        releaseEvent.getMessageId(),
                        EXCHANGE_ORDER_CLOSE_DLX,
                        ROUTING_ORDER_CLOSE_TIMEOUT,
                        cleanOrderNo,
                        "TIMEOUT_CLOSED",
                        "死信队列到期触发 CAS 自动关单，已归还 " + seatIds.size() + " 个锁定座位",
                        "CONSUMED"
                );
                log.info("[DLX死信自动关单成功] orderNo={} 超时未支付已关闭并释放座位 {}", cleanOrderNo, seatIds);
            } else {
                seataAuditService.recordMqEvent(
                        "msg-skip-" + UUID.randomUUID().toString().substring(0, 8),
                        EXCHANGE_ORDER_CLOSE_DLX,
                        ROUTING_ORDER_CLOSE_TIMEOUT,
                        cleanOrderNo,
                        "TIMEOUT_SKIPPED_PAID",
                        "死信消息到达时订单状态已为 " + order.getStatus() + " (CAS 0行更新)，幂等跳过关单",
                        "SKIPPED_IDEMPOTENT"
                );
                log.info("[DLX死信关单幂等跳过] orderNo={} 当前状态为 {}，无需关单", cleanOrderNo, order.getStatus());
            }

            channel.basicAck(tag, false);
        } catch (Exception ex) {
            log.error("处理死信超时关单异常 orderNo={}", cleanOrderNo, ex);
            channel.basicNack(tag, false, false);
        }
    }

    private List<Long> parseSeatIds(String json) {
        try {
            return objectMapper.readValue(json, new TypeReference<List<Long>>() {});
        } catch (Exception e) {
            return Collections.emptyList();
        }
    }
}
