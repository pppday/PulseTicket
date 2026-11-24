package com.pulseticket.show.mq;

import com.pulseticket.feign.dto.OrderClosedEventDTO;
import com.pulseticket.feign.dto.OrderPaidEventDTO;
import com.pulseticket.show.service.ShowCacheAndSeatService;
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
 * show-service 的 RabbitMQ 消费者与队列声明配置：
 * 1. 监听支付成功广播队列（`ticket.show.paid.queue`）：收到后幂等把座位状态从 `LOCKED` 正式改为 `SOLD`；
 * 2. 监听关单/退票释座队列（`ticket.show.closed.release.queue`）：收到后幂等把座位归还为 `AVAILABLE`，并同步清理 Redis 座位图缓存。
 * 所有监听器均采用手动 ACK，确保消息处理成功后再确认消费。
 */
@Configuration
@RequiredArgsConstructor
public class ShowMqConfigAndListener {

    private static final Logger log = LoggerFactory.getLogger(ShowMqConfigAndListener.class);

    public static final String EXCHANGE_ORDER_PAID_FANOUT = "ticket.order.paid.fanout";
    public static final String QUEUE_SHOW_PAID_SOLD = "ticket.show.paid.queue";

    public static final String EXCHANGE_ORDER_CLOSED_DIRECT = "ticket.order.closed.direct";
    public static final String QUEUE_SHOW_CLOSED_RELEASE = "ticket.show.closed.release.queue";
    public static final String ROUTING_ORDER_CLOSED = "order.closed.release";

    private final ShowCacheAndSeatService showService;

    @Bean
    public MessageConverter jacksonMessageConverter() {
        return new Jackson2JsonMessageConverter();
    }

    @Bean
    public FanoutExchange orderPaidFanoutExchange() {
        return new FanoutExchange(EXCHANGE_ORDER_PAID_FANOUT, true, false);
    }

    @Bean
    public Queue showPaidSoldQueue() {
        return QueueBuilder.durable(QUEUE_SHOW_PAID_SOLD).build();
    }

    @Bean
    public Binding bindShowPaidQueue(Queue showPaidSoldQueue, FanoutExchange orderPaidFanoutExchange) {
        return BindingBuilder.bind(showPaidSoldQueue).to(orderPaidFanoutExchange);
    }

    @Bean
    public DirectExchange orderClosedDirectExchange() {
        return new DirectExchange(EXCHANGE_ORDER_CLOSED_DIRECT, true, false);
    }

    @Bean
    public Queue showClosedReleaseQueue() {
        return QueueBuilder.durable(QUEUE_SHOW_CLOSED_RELEASE).build();
    }

    @Bean
    public Binding bindShowClosedReleaseQueue(Queue showClosedReleaseQueue, DirectExchange orderClosedDirectExchange) {
        return BindingBuilder.bind(showClosedReleaseQueue).to(orderClosedDirectExchange).with(ROUTING_ORDER_CLOSED);
    }

    @RabbitListener(queues = QUEUE_SHOW_PAID_SOLD)
    public void onOrderPaid(OrderPaidEventDTO event, Message message, Channel channel) throws IOException {
        long tag = message.getMessageProperties().getDeliveryTag();
        try {
            showService.confirmSeatsSoldIdempotent(event);
            channel.basicAck(tag, false);
        } catch (Exception ex) {
            log.error("处理支付成功座位转 SOLD 消息失败 orderNo={}", event != null ? event.getOrderNo() : "null", ex);
            channel.basicNack(tag, false, false);
        }
    }

    @RabbitListener(queues = QUEUE_SHOW_CLOSED_RELEASE)
    public void onOrderClosed(OrderClosedEventDTO event, Message message, Channel channel) throws IOException {
        long tag = message.getMessageProperties().getDeliveryTag();
        try {
            showService.releaseSeatsIdempotent(event);
            channel.basicAck(tag, false);
        } catch (Exception ex) {
            log.error("处理超时关单释放座位消息失败 orderNo={}", event != null ? event.getOrderNo() : "null", ex);
            channel.basicNack(tag, false, false);
        }
    }
}
