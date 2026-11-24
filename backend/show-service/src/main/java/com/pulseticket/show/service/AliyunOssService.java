package com.pulseticket.show.service;

import com.pulseticket.common.exception.BizException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 轻量级阿里云 OSS 直传服务（零重型 SDK 依赖，直接用 JDK HttpClient + HmacSHA1 签名实现）。
 * 负责后台演出海报图片的上传、公网可访问 URL 拼接以及拉取 OSS 图库历史文件列表。
 */
@Service
public class AliyunOssService {

    private static final Logger log = LoggerFactory.getLogger(AliyunOssService.class);

    private static final DateTimeFormatter GMT_FMT =
            DateTimeFormatter.ofPattern("EEE, dd MMM yyyy HH:mm:ss 'GMT'", Locale.US).withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter DATE_DIR_FMT = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final Pattern CONTENTS_PATTERN = Pattern.compile("<Contents>(.*?)</Contents>", Pattern.DOTALL);
    private static final Pattern KEY_PATTERN = Pattern.compile("<Key>(.*?)</Key>");
    private static final Pattern SIZE_PATTERN = Pattern.compile("<Size>(\\d+)</Size>");
    private static final Pattern MODIFIED_PATTERN = Pattern.compile("<LastModified>(.*?)</LastModified>");

    @Value("${pulseticket.oss.endpoint:oss-cn-beijing.aliyuncs.com}")
    private String endpoint;

    @Value("${pulseticket.oss.bucket:ticket-ppday}")
    private String bucket;

    @Value("${pulseticket.oss.access-key-id:${OSS_ACCESS_KEY_ID:}}")
    private String accessKeyId;

    @Value("${pulseticket.oss.access-key-secret:${OSS_ACCESS_KEY_SECRET:}}")
    private String accessKeySecret;

    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    public Map<String, Object> uploadPoster(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new BizException(400, "请选择要上传的演出海报图片文件");
        }
        if (file.getSize() > 12 * 1024 * 1024L) {
            throw new BizException(400, "海报文件大小不能超过 12MB");
        }
        String originalName = Optional.ofNullable(file.getOriginalFilename()).orElse("poster.jpg");
        String ext = ".jpg";
        int dot = originalName.lastIndexOf('.');
        if (dot >= 0 && dot < originalName.length() - 1) {
            ext = originalName.substring(dot).toLowerCase(Locale.ROOT);
        }
        if (!Set.of(".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif", ".avif").contains(ext)) {
            throw new BizException(400, "仅支持 JPG / PNG / WebP / SVG / GIF 格式的演出海报");
        }

        String contentType = Optional.ofNullable(file.getContentType()).orElse(guessContentType(ext));
        String objectKey = "posters/" + LocalDate.now().format(DATE_DIR_FMT) + "/"
                + UUID.randomUUID().toString().replace("-", "").substring(0, 12) + ext;

        try {
            byte[] bytes = file.getBytes();
            String publicUrl = uploadBytes(objectKey, bytes, contentType);
            Map<String, Object> res = new LinkedHashMap<>();
            res.put("url", publicUrl);
            res.put("objectKey", objectKey);
            res.put("bucket", bucket);
            res.put("endpoint", endpoint);
            res.put("size", bytes.length);
            res.put("contentType", contentType);
            res.put("originalFilename", originalName);
            return res;
        } catch (BizException e) {
            throw e;
        } catch (Exception e) {
            log.error("[AliyunOSS] Upload failed for {}", originalName, e);
            throw new BizException(500, "上传阿里云 OSS 失败: " + e.getMessage());
        }
    }

    public String uploadBytes(String objectKey, byte[] bytes, String contentType) {
        try {
            String dateGmt = GMT_FMT.format(Instant.now());
            String canonicalResource = "/" + bucket + "/" + objectKey;
            String stringToSign = "PUT\n\n"
                    + contentType + "\n"
                    + dateGmt + "\n"
                    + "x-oss-object-acl:public-read\n"
                    + canonicalResource;

            String signature = signHmacSha1(stringToSign);
            String host = bucket + "." + endpoint;
            String url = "https://" + host + "/" + encodeObjectKey(objectKey);

            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(20))
                    .header("Date", dateGmt)
                    .header("Content-Type", contentType)
                    .header("x-oss-object-acl", "public-read")
                    .header("Authorization", "OSS " + accessKeyId + ":" + signature)
                    .PUT(HttpRequest.BodyPublishers.ofByteArray(bytes))
                    .build();

            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() < 200 || resp.statusCode() >= 300) {
                log.error("[AliyunOSS] PUT {} failed with HTTP {}: {}", url, resp.statusCode(), resp.body());
                throw new BizException(502, "阿里云 OSS 响应异常 (HTTP " + resp.statusCode() + ")");
            }
            String publicUrl = "https://" + host + "/" + objectKey;
            log.info("[AliyunOSS] Uploaded object -> {} ({} bytes)", publicUrl, bytes.length);
            return publicUrl;
        } catch (BizException e) {
            throw e;
        } catch (Exception e) {
            throw new BizException(500, "阿里云 OSS 直传异常: " + e.getMessage());
        }
    }

    public Map<String, Object> listBucketPosters() {
        List<Map<String, Object>> files = new ArrayList<>();
        try {
            String dateGmt = GMT_FMT.format(Instant.now());
            String stringToSign = "GET\n\n\n" + dateGmt + "\n/" + bucket + "/";
            String signature = signHmacSha1(stringToSign);
            String host = bucket + "." + endpoint;
            String listUrl = "https://" + host + "/?prefix=posters/&max-keys=50";

            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(listUrl))
                    .timeout(Duration.ofSeconds(10))
                    .header("Date", dateGmt)
                    .header("Authorization", "OSS " + accessKeyId + ":" + signature)
                    .GET()
                    .build();

            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 200 && resp.body() != null) {
                Matcher m = CONTENTS_PATTERN.matcher(resp.body());
                while (m.find()) {
                    String block = m.group(1);
                    String key = extractXmlTag(KEY_PATTERN, block);
                    String sizeStr = extractXmlTag(SIZE_PATTERN, block);
                    String lastModified = extractXmlTag(MODIFIED_PATTERN, block);
                    if (key != null && !key.endsWith("/")) {
                        Map<String, Object> item = new LinkedHashMap<>();
                        item.put("objectKey", key);
                        item.put("url", "https://" + host + "/" + key);
                        item.put("size", sizeStr != null ? Long.parseLong(sizeStr) : 0L);
                        item.put("lastModified", lastModified);
                        files.add(item);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("[AliyunOSS] Failed to list bucket objects: {}", e.getMessage());
        }
        files.sort((a, b) -> String.valueOf(b.get("lastModified")).compareTo(String.valueOf(a.get("lastModified"))));

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("bucket", bucket);
        result.put("endpoint", endpoint);
        result.put("domain", "https://" + bucket + "." + endpoint);
        result.put("region", "华北2 (北京)");
        result.put("files", files);
        return result;
    }

    private String signHmacSha1(String data) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA1");
        mac.init(new SecretKeySpec(accessKeySecret.getBytes(StandardCharsets.UTF_8), "HmacSHA1"));
        return Base64.getEncoder().encodeToString(mac.doFinal(data.getBytes(StandardCharsets.UTF_8)));
    }

    private String encodeObjectKey(String key) {
        String[] parts = key.split("/");
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            if (i > 0) sb.append('/');
            sb.append(URLEncoder.encode(parts[i], StandardCharsets.UTF_8).replace("+", "%20"));
        }
        return sb.toString();
    }

    private String guessContentType(String ext) {
        return switch (ext) {
            case ".png" -> "image/png";
            case ".webp" -> "image/webp";
            case ".svg" -> "image/svg+xml";
            case ".gif" -> "image/gif";
            case ".avif" -> "image/avif";
            default -> "image/jpeg";
        };
    }

    private String extractXmlTag(Pattern p, String xml) {
        Matcher m = p.matcher(xml);
        return m.find() ? m.group(1) : null;
    }
}
