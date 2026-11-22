package com.pulseticket.user.mq;

import com.pulseticket.feign.dto.OrderPaidEventDTO;
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

@Configuration
@RequiredArgsConstructor
public class UserMqConfigAndListener {

    private static final Logger log = LoggerFactory.getLogger(UserMqConfigAndListener.class);

    public static final String EXCHANGE_ORDER_PAID_FANOUT = "ticket.order.paid.fanout";
    public static final String QUEUE_USER_PAID_REWARD = "ticket.user.paid.queue";

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
}
