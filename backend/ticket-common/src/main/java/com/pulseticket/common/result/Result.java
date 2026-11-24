package com.pulseticket.common.result;

import com.pulseticket.common.context.UserContext;
import lombok.Getter;
import lombok.Setter;

import java.io.Serializable;
import java.util.UUID;

/**
 * 统一微服务 REST / Feign 响应包装体
 * 作用：全系统所有接口（网关拦截报错、业务接口成功/失败、Feign 跨服务调用）都返回这个标准信封格式，
 * 自动带上当前线程的 traceId 和响应时间戳，方便前端统一弹窗和运维查日志。
 */
@Getter
@Setter
public class Result<T> implements Serializable {

    /** 业务状态码（200 表示成功，其余均为失败错误码） */
    private int code;
    /** 给用户或调用方看的提示文案 */
    private String message;
    /** 实际业务数据载荷 */
    private T data;
    /** 全链路追踪 ID，用于串联跨微服务日志 */
    private String traceId;
    /** 响应生成的毫秒时间戳 */
    private long timestamp;

    public Result() {
        this.timestamp = System.currentTimeMillis();
    }

    public Result(int code, String message, T data, String traceId) {
        this.code = code;
        this.message = message;
        this.data = data;
        this.traceId = traceId != null ? traceId : resolveTraceId();
        this.timestamp = System.currentTimeMillis();
    }

    /** 优先从当前线程 UserContext 取网关传来的 traceId，取不到则兜底现生成一个 */
    private static String resolveTraceId() {
        String ctxTrace = UserContext.getTraceId();
        if (ctxTrace != null && !ctxTrace.isBlank()) {
            return ctxTrace;
        }
        return "tr-" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    /** 返回无数据的成功响应 */
    public static <T> Result<T> ok() {
        return new Result<>(200, "OK", null, resolveTraceId());
    }

    /** 返回带业务数据的成功响应 */
    public static <T> Result<T> ok(T data) {
        return new Result<>(200, "OK", data, resolveTraceId());
    }

    /** 返回带自定义提示语与数据的成功响应 */
    public static <T> Result<T> ok(String message, T data) {
        return new Result<>(200, message, data, resolveTraceId());
    }

    /** 返回指定错误码的失败响应 */
    public static <T> Result<T> fail(int code, String message) {
        return new Result<>(code, message, null, resolveTraceId());
    }

    /** 返回默认 500 错误码的失败响应 */
    public static <T> Result<T> fail(String message) {
        return new Result<>(500, message, null, resolveTraceId());
    }

    /** 快捷判断本次调用是否成功（Feign 调用方常用它检查下游是否执行成功） */
    public boolean isSuccess() {
        return this.code == 200;
    }
}
