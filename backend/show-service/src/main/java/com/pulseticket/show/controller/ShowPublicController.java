package com.pulseticket.show.controller;

import com.pulseticket.common.context.UserContext;
import com.pulseticket.common.exception.BizException;
import com.pulseticket.common.result.Result;
import com.pulseticket.show.entity.ShowInfo;
import com.pulseticket.show.service.AliyunOssService;
import com.pulseticket.show.service.ShowCacheAndSeatService;
import lombok.Data;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/show")
@RequiredArgsConstructor
public class ShowPublicController {

    private final ShowCacheAndSeatService showService;
    private final AliyunOssService aliyunOssService;

    @Data
    public static class SeatQuoteRequest {
        private List<Long> seatIds;
    }

    @GetMapping("/public/list")
    public Result<List<Map<String, Object>>> listShows(@RequestParam(required = false) String category) {
        return Result.ok(showService.listShows(category));
    }

    @GetMapping("/public/detail/{id}")
    public Result<Map<String, Object>> getShowDetail(@PathVariable Long id) {
        return Result.ok(showService.getShowDetailWithCacheProtection(id));
    }

    @GetMapping("/public/sessions/{sessionId}/seats")
    public Result<Map<String, Object>> getSessionSeats(@PathVariable Long sessionId) {
        return Result.ok(showService.getSessionSeatMap(sessionId));
    }

    @PostMapping("/public/sessions/{sessionId}/quote")
    public Result<Map<String, Object>> quoteSessionSeats(
            @PathVariable Long sessionId,
            @RequestBody SeatQuoteRequest req
    ) {
        return Result.ok(showService.quoteSessionSeats(sessionId, req != null ? req.getSeatIds() : null));
    }

    @PostMapping("/cache/evict/{showId}")
    public Result<String> evictCache(@PathVariable Long showId) {
        requireAdmin("仅限架构运维专员执行缓存驱逐演练");
        showService.evictShowCaches(showId, null);
        return Result.ok("已清除 Redis 演出详情缓存 key=ticket:cache:show:" + showId);
    }

    @GetMapping("/admin/list")
    public Result<List<Map<String, Object>>> listAdminShows(@RequestParam(required = false) String category) {
        requireAdmin("仅限管理员查看全量演出目录（含下架归档演出）");
        return Result.ok(showService.listAllShowsForAdmin(category));
    }

    @GetMapping("/admin/options")
    public Result<Map<String, Object>> getAdminOptions() {
        requireAdmin("仅限管理员读取数据库城市、场馆与演艺厅下拉字典");
        return Result.ok(showService.getAdminFormOptions());
    }

    @PostMapping("/admin/reorder")
    public Result<List<Map<String, Object>>> reorderShows(@RequestBody Map<String, List<Long>> body) {
        requireAdmin("仅限管理员执行演出排序调整");
        List<Long> orderedIds = body != null ? body.get("orderedIds") : List.of();
        return Result.ok("演出排序已写入数据库并同步刷新 Redis 缓存", showService.reorderShows(orderedIds));
    }

    @PostMapping("/admin/oss/upload")
    public Result<Map<String, Object>> uploadPosterToOss(@RequestParam("file") MultipartFile file) {
        requireAdmin("仅限管理员上传演出海报至阿里云 OSS (ticket-ppday)");
        return Result.ok("海报已成功上传至阿里云 OSS (华北2·北京)", aliyunOssService.uploadPoster(file));
    }

    @GetMapping("/admin/oss/list")
    public Result<Map<String, Object>> listOssPosters() {
        requireAdmin("仅限管理员读取阿里云 OSS 图库列表");
        return Result.ok(aliyunOssService.listBucketPosters());
    }

    @PostMapping("/admin/save")
    public Result<Map<String, Object>> saveShow(@RequestBody Map<String, Object> payload) {
        requireAdmin("仅限管理员动态新增或编辑演出与场次信息");
        Map<String, Object> res = showService.saveOrUpdateShow(payload);
        boolean isNew = Boolean.TRUE.equals(res.get("isNew"));
        return Result.ok(isNew ? "新演出及 48 座全息选座厅已创建并上架" : "演出信息与 OSS 海报已更新，Redis 缓存已同步", res);
    }

    @PostMapping("/admin/status/{id}")
    public Result<ShowInfo> toggleShowStatus(@PathVariable Long id, @RequestBody Map<String, String> body) {
        requireAdmin("仅限管理员执行演出上下架操作");
        String status = body != null ? body.get("status") : "ON_SALE";
        ShowInfo updated = showService.updateShowStatus(id, status);
        String msg = "OFF_SHELF".equalsIgnoreCase(updated.getStatus())
                ? "演出已下架归档，C端购票大厅已隐藏"
                : "演出已重新上架热售，C端购票大厅已同步";
        return Result.ok(msg, updated);
    }

    @DeleteMapping("/admin/{id}")
    public Result<String> deleteShow(@PathVariable Long id) {
        requireAdmin("仅限管理员删除演出记录");
        showService.deleteShow(id);
        return Result.ok("演出及关联场次库存已彻底删除");
    }

    @GetMapping("/admin/analytics")
    public Result<Map<String, Object>> getAdminAnalytics() {
        requireAdmin("仅限架构运维专员查看票房与场次库存统计");
        return Result.ok(showService.getAdminSalesAnalytics());
    }

    @GetMapping("/admin/db-overview")
    public Result<Map<String, Object>> getAdminDbOverview() {
        requireAdmin("仅限架构运维专员查看演出与座位库存数据库");
        return Result.ok(showService.getAdminDbOverview());
    }

    private void requireAdmin(String message) {
        if (!UserContext.isAdmin()) {
            throw new BizException(403, message);
        }
    }
}
