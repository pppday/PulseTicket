package com.pulseticket.common.exception;

/**
 * 通用业务异常类（可预期的业务校验失败，如“余票不足”“无权操作”“未登录”等）
 * 作用：业务代码里直接 throw new BizException(409, "座位已被抢光") 就能中断流程并触发事务回滚，
 * 然后由 GlobalExceptionHandler 统一接住并转成友好的 HTTP 状态码和 JSON 提示给前端。
 */
public class BizException extends RuntimeException {

    /** 业务/HTTP 状态码（默认 400 参数或业务错误，常见如 401 未登录、403 无权限、404 不存在、409 并发冲突） */
    private final int code;

    /** 默认 400 状态码的业务异常构造方法 */
    public BizException(String message) {
        super(message);
        this.code = 400;
    }

    /** 自定义状态码的业务异常构造方法 */
    public BizException(int code, String message) {
        super(message);
        this.code = code;
    }

    public int getCode() {
        return code;
    }
}
