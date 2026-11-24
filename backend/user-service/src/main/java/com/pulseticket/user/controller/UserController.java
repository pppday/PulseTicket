package com.pulseticket.user.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.pulseticket.common.context.UserContext;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.common.result.Result;
import com.pulseticket.common.security.JwtUtils;
import com.pulseticket.user.entity.PointsLog;
import com.pulseticket.user.entity.UserAccount;
import com.pulseticket.user.entity.UserMessage;
import com.pulseticket.user.mapper.PointsLogMapper;
import com.pulseticket.user.mapper.UserAccountMapper;
import com.pulseticket.user.mapper.UserMessageMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 用户认证、个人档案、站内信与后台会员目录 REST 入口（端口 :8201）。
 *
 * 通俗理解：
 * - 登录/注册在网关白名单内，验证密码（BCrypt）通过后签发 JWT Token；
 * - 个人资料、修改实名手机号、消息已读全部信任网关验签后透传的 `UserContext`，避免业务接口重复解析 JWT。
 */
@RestController
@RequestMapping("/api/user")
@RequiredArgsConstructor
public class UserController {

    private static final DateTimeFormatter DT_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final UserAccountMapper userAccountMapper;
    private final PointsLogMapper pointsLogMapper;
    private final UserMessageMapper userMessageMapper;
    private final JdbcTemplate jdbcTemplate;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    @Value("${pulseticket.jwt.secret:PulseTicketMicroserviceGatewayJwtSecretKey2026EnterpriseEdition8899}")
    private String jwtSecret;

    @PostMapping("/auth/login")
    public Result<Map<String, Object>> login(@RequestBody Map<String, String> body) {
        String username = body.getOrDefault("username", "").trim();
        String password = body.getOrDefault("password", "").trim();
        if (username.isEmpty() || password.isEmpty()) {
            throw new BizException("请输入用户名和密码");
        }

        UserAccount user = userAccountMapper.selectOne(
                new LambdaQueryWrapper<UserAccount>().eq(UserAccount::getUsername, username)
        );
        if (user == null || !verifyPassword(user, password)) {
            throw new BizException(401, "账号或密码不正确");
        }

        String role = (user.getRole() != null && !user.getRole().isBlank()) ? user.getRole() : "USER";
        String token = JwtUtils.generateToken(user.getId(), user.getUsername(), user.getVipLevel(), role, jwtSecret);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("accessToken", token);
        data.put("user", toUserVO(user));
        return Result.ok("登录成功", data);
    }

    @PostMapping("/auth/register")
    @Transactional(rollbackFor = Exception.class)
    public Result<Map<String, Object>> register(@RequestBody Map<String, String> body) {
        String username = body.getOrDefault("username", "").trim();
        String password = body.getOrDefault("password", "").trim();
        String nickname = body.getOrDefault("nickname", "").trim();
        String phone = body.getOrDefault("phone", "").trim();

        if (!username.matches("^[a-zA-Z0-9_]{3,24}$")) {
            throw new BizException("用户名需为 3~24 位字母、数字或下划线");
        }
        if (phone.isEmpty()) {
            throw new BizException("手机号为必填项，请输入 11 位真实手机号");
        }
        if (!phone.matches("^1[3-9]\\d{9}$")) {
            throw new BizException("请输入正确的 11 位中国大陆手机号");
        }
        if (password.length() < 6) {
            throw new BizException("登录密码长度不能少于 6 位");
        }

        Long exists = userAccountMapper.selectCount(
                new LambdaQueryWrapper<UserAccount>().eq(UserAccount::getUsername, username)
        );
        if (exists != null && exists > 0) {
            throw new BizException(409, "该用户名已被注册，请换一个");
        }

        UserAccount user = new UserAccount();
        user.setUsername(username);
        user.setPasswordHash(passwordEncoder.encode(password));
        user.setNickname(nickname.isEmpty() ? username : nickname);
        user.setPhone(phone);
        user.setVipLevel("NORMAL");
        user.setRole("USER");
        user.setPointsBalance(100);
        user.setTotalOrders(0);
        userAccountMapper.insert(user);

        PointsLog bonus = new PointsLog();
        bonus.setUserId(user.getId());
        bonus.setOrderNo("REG-" + user.getId());
        bonus.setBizType("SIGN_IN");
        bonus.setPointsDelta(100);
        bonus.setBalanceAfter(100);
        bonus.setRemark("新会员注册礼遇积分");
        pointsLogMapper.insert(bonus);

        UserMessage welcome = new UserMessage();
        welcome.setUserId(user.getId());
        welcome.setOrderNo("WELCOME-" + user.getId());
        welcome.setMsgType("SYSTEM");
        welcome.setTitle("欢迎加入 PulseTicket");
        welcome.setContent("您的账号已开通，已赠送 100 新人观演积分。选座下单后座位将为您保留 15 分钟。");
        welcome.setIsRead(0);
        userMessageMapper.insert(welcome);

        String token = JwtUtils.generateToken(user.getId(), user.getUsername(), user.getVipLevel(), "USER", jwtSecret);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("accessToken", token);
        data.put("user", toUserVO(user));
        return Result.ok("注册成功", data);
    }

    @GetMapping("/profile")
    public Result<Map<String, Object>> getProfile() {
        Long userId = UserContext.getUserId();
        if (userId == null) {
            throw new BizException(401, "未获取到网关透传的登录态");
        }
        UserAccount user = userAccountMapper.selectById(userId);
        if (user == null) {
            throw new BizException(401, "用户不存在或登录已失效");
        }

        List<PointsLog> logs = pointsLogMapper.selectList(
                new LambdaQueryWrapper<PointsLog>()
                        .eq(PointsLog::getUserId, userId)
                        .orderByDesc(PointsLog::getCreatedAt)
                        .last("LIMIT 20")
        );

        List<UserMessage> messages = userMessageMapper.selectList(
                new LambdaQueryWrapper<UserMessage>()
                        .eq(UserMessage::getUserId, userId)
                        .orderByDesc(UserMessage::getCreatedAt)
                        .last("LIMIT 20")
        );

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("user", toUserVO(user));
        data.put("threadLocalUserId", userId);
        data.put("threadLocalUsername", UserContext.getUsername());
        data.put("threadLocalRole", UserContext.getRole());
        data.put("traceId", UserContext.getTraceId());
        data.put("pointsLogs", logs);
        data.put("messages", messages);
        return Result.ok(data);
    }

    @PostMapping("/profile/phone")
    public Result<Map<String, Object>> updatePhone(@RequestBody Map<String, String> body) {
        Long userId = UserContext.getUserId();
        if (userId == null) {
            throw new BizException(401, "未获取到网关透传的登录态");
        }
        String phone = body.getOrDefault("phone", "").trim();
        if (phone.isEmpty()) {
            throw new BizException("手机号不能为空");
        }
        if (!phone.matches("^1[3-9]\\d{9}$")) {
            throw new BizException("请输入正确的 11 位中国大陆手机号");
        }
        UserAccount user = userAccountMapper.selectById(userId);
        if (user == null) {
            throw new BizException(404, "用户不存在");
        }
        user.setPhone(phone);
        userAccountMapper.updateById(user);
        UserAccount refreshed = userAccountMapper.selectById(userId);
        return Result.ok("实名绑定手机号已更新", Map.of("user", toUserVO(refreshed)));
    }

    @PostMapping("/messages/{id}/read")
    public Result<Void> markMessageRead(@PathVariable Long id) {
        Long userId = UserContext.getUserId();
        UserMessage msg = userMessageMapper.selectById(id);
        if (msg != null && msg.getUserId().equals(userId)) {
            msg.setIsRead(1);
            userMessageMapper.updateById(msg);
        }
        return Result.ok();
    }

    /**
     * ADMIN 专属：读取 ticket_user 库全表元数据与实时数据明细
     */
    @GetMapping("/admin/db-overview")
    public Result<Map<String, Object>> getAdminDbOverview() {
        if (!UserContext.isAdmin()) {
            throw new BizException(403, "仅限架构运维专员查看用户与积分数据库底层数据");
        }

        List<UserAccount> users = userAccountMapper.selectList(
                new LambdaQueryWrapper<UserAccount>().orderByAsc(UserAccount::getId)
        );
        List<Map<String, Object>> userRows = new ArrayList<>();
        for (UserAccount u : users) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", u.getId());
            row.put("username", u.getUsername());
            row.put("nickname", u.getNickname());
            row.put("phone", u.getPhone());
            row.put("vipLevel", u.getVipLevel());
            row.put("role", (u.getRole() != null && !u.getRole().isBlank()) ? u.getRole() : "USER");
            row.put("pointsBalance", u.getPointsBalance());
            row.put("totalOrders", u.getTotalOrders());
            String ph = u.getPasswordHash();
            row.put("passwordAlgo", (ph != null && ph.startsWith("$2")) ? "BCrypt (" + ph.substring(0, Math.min(7, ph.length())) + "...)" : "RAW");
            row.put("createdAt", u.getCreatedAt() != null ? u.getCreatedAt().format(DT_FMT) : null);
            row.put("updatedAt", u.getUpdatedAt() != null ? u.getUpdatedAt().format(DT_FMT) : null);
            userRows.add(row);
        }

        List<PointsLog> pointsLogs = pointsLogMapper.selectList(
                new LambdaQueryWrapper<PointsLog>()
                        .orderByDesc(PointsLog::getId)
                        .last("LIMIT 200")
        );

        List<UserMessage> userMessages = userMessageMapper.selectList(
                new LambdaQueryWrapper<UserMessage>()
                        .orderByDesc(UserMessage::getId)
                        .last("LIMIT 200")
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

        List<Map<String, Object>> tablesMeta = queryTablesMeta(List.of(
                "user_account", "points_log", "user_message", "undo_log"
        ));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("database", "ticket_user");
        data.put("service", "user-service (:8201)");
        data.put("tablesMeta", tablesMeta);
        data.put("userAccounts", userRows);
        data.put("pointsLogs", pointsLogs);
        data.put("userMessages", userMessages);
        data.put("undoLogs", undoLogs);
        return Result.ok(data);
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

    private boolean verifyPassword(UserAccount user, String rawPassword) {
        String stored = user.getPasswordHash();
        if (stored == null) return false;
        if (stored.startsWith("$2a$") || stored.startsWith("$2b$")) {
            return passwordEncoder.matches(rawPassword, stored);
        }
        if (stored.equals(rawPassword)) {
            user.setPasswordHash(passwordEncoder.encode(rawPassword));
            userAccountMapper.updateById(user);
            return true;
        }
        return false;
    }

    private Map<String, Object> toUserVO(UserAccount u) {
        Map<String, Object> vo = new LinkedHashMap<>();
        vo.put("id", u.getId());
        vo.put("username", u.getUsername());
        vo.put("nickname", u.getNickname());
        vo.put("phone", u.getPhone());
        vo.put("vipLevel", u.getVipLevel());
        vo.put("role", (u.getRole() != null && !u.getRole().isBlank()) ? u.getRole() : "USER");
        vo.put("pointsBalance", u.getPointsBalance());
        vo.put("totalOrders", u.getTotalOrders());
        return vo;
    }
}
