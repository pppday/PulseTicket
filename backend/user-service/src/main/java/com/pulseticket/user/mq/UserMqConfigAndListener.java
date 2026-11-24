package com.pulseticket.user.mq;

import com.pulseticket.feign.dto.OrderPaidEventDTO;
import com.pulseticket.feign.dto.OrderRefundedEventDTO;
import com.pulseticket.user.service.UserPointsService;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.*;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;

/**
 * user-service 的 RabbitMQ 广播消费者配置：
 * 1. 订阅 `ticket.order.paid.fanout`：订单支付成功后异步加积分、发电子票站内信（与下单主链路解耦）；
 * 2. 订阅 `ticket.order.refunded.fanout`：已支付订单退票后异步冲正扣回积分、发退票站内信（防白嫖刷分）。
 */
@Configuration
@RequiredArgsConstructor
public class UserMqConfigAndListener {

    private static final Logger log = LoggerFactory.getLogger(UserMqConfigAndListener.class);

    public static final String EXCHANGE_ORDER_PAID_FANOUT = "ticket.order.paid.fanout";
    public static final String QUEUE_USER_PAID_REWARD = "ticket.user.paid.queue";

    public static final String EXCHANGE_ORDER_REFUNDED_FANOUT = "ticket.order.refunded.fanout";
    public static final String QUEUE_USER_REFUND_DEDUCT = "ticket.user.refund.queue";

    private final UserPointsService userPointsService;

    @Bean
    public MessageConverter jacksonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public FanoutExchange orderPaidFanoutExchange() {
        return new FanoutExchange(EXCHANGE_ORDER_PAID_FANOUT, true, false);
    }

    @Bean
    public Queue userPaidRewardQueue() {
        return QueueBuilder.durable(QUEUE_USER_PAID_REWARD).build();
    }

    @Bean
    public Binding bindUserPaidQueue(Queue userPaidRewardQueue, FanoutExchange orderPaidFanoutExchange) {
        return BindingBuilder.bind(userPaidRewardQueue).to(orderPaidFanoutExchange);
    }

    @Bean
    public FanoutExchange orderRefundedFanoutExchange() {
        return new FanoutExchange(EXCHANGE_ORDER_REFUNDED_FANOUT, true, false);
    }

    @Bean
    public Queue userRefundDeductQueue() {
        return QueueBuilder.durable(QUEUE_USER_REFUND_DEDUCT).build();
    }

    @Bean
    public Binding bindUserRefundQueue(Queue userRefundDeductQueue, FanoutExchange orderRefundedFanoutExchange) {
        return BindingBuilder.bind(userRefundDeductQueue).to(orderRefundedFanoutExchange);
    }

    @RabbitListener(queues = QUEUE_USER_PAID_REWARD)
    public void onOrderPaidMessage(OrderPaidEventDTO event, Message message, Channel channel) throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            userPointsService.processOrderPaidRewardIdempotent(event);
            // 业务真正落库成功（或唯一索引幂等跳过）后，手动回传 ACK 确保消息零丢失
            channel.basicAck(deliveryTag, false);
        } catch (Exception ex) {
            log.error("处理订单支付积分消息异常 orderNo={}", event != null ? event.getOrderNo() : "null", ex);
            // 拒绝且不无限重回队首死循环
            channel.basicNack(deliveryTag, false, false);
        }
    }

    @RabbitListener(queues = QUEUE_USER_REFUND_DEDUCT)
    public void onOrderRefundedMessage(OrderRefundedEventDTO event, Message message, Channel channel) throws IOException {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            userPointsService.processOrderRefundDeductIdempotent(event);
            channel.basicAck(deliveryTag, false);
        } catch (Exception ex) {
            log.error("处理订单退票积分冲正消息异常 orderNo={}", event != null ? event.getOrderNo() : "null", ex);
            channel.basicNack(deliveryTag, false, false);
        }
    }
}
