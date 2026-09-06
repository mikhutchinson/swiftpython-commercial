#include <metal_stdlib>
using namespace metal;

struct Camera {
    float pitch;
    float yaw;
    float scale;
    float density;
};
struct Point {
    float4 position [[position]];
    float size [[point_size]];
    float3 color;
};

vertex Point particle_vertex(uint id [[vertex_id]],
                            const device float4 *particles [[buffer(0)]],
                            constant Camera &camera [[buffer(1)]]) {
    float4 particle = particles[id];
    float3 p = particle.xyz * camera.scale;
    float cx = cos(camera.pitch), sx = sin(camera.pitch);
    p = float3(p.x, p.y * cx - p.z * sx, p.y * sx + p.z * cx);
    float cy = cos(camera.yaw), sy = sin(camera.yaw);
    p = float3(p.x * cy + p.z * sy, p.y, -p.x * sy + p.z * cy);
    float perspective = 7.5 / max(3.0, 7.5 - p.z);
    Point out;
    out.position = float4(p.x * perspective / 4.55,
                          p.y * perspective / 2.56 - 0.04, 0.5, 1);
    out.size = clamp((1.9 + particle.w * 1.1) * perspective, 1.0, 5.0);
    float3 blue = float3(0.12, 0.44, 1.0);
    float3 gold = float3(1.0, 0.30, 0.065);
    float tint = smoothstep(0.15, 0.95, particle.w);
    out.color = mix(blue, gold, tint) * camera.density;
    return out;
}

fragment float4 particle_fragment(Point point [[stage_in]],
                                  float2 uv [[point_coord]]) {
    float2 q = uv * 2 - 1;
    float r2 = dot(q, q);
    if (r2 > 1) discard_fragment();
    float light = exp(-r2 * 4.0) * 0.20;
    return float4(point.color * light, 1);
}

struct Quad { float4 position [[position]]; float2 uv; };
vertex Quad full_screen(uint id [[vertex_id]]) {
    float2 p = float2((id << 1) & 2, id & 2);
    return {float4(p * 2 - 1, 0, 1), float2(p.x, 1 - p.y)};
}

fragment float4 develop(Quad in [[stage_in]], texture2d<float> light [[texture(0)]]) {
    constexpr sampler s(coord::normalized, address::clamp_to_edge, filter::linear);
    float2 pixel = 1.0 / float2(light.get_width(), light.get_height());
    float3 value = light.sample(s, in.uv).rgb;
    float3 bloom = float3(0);
    for (int y = -1; y <= 1; ++y)
        for (int x = -1; x <= 1; ++x)
            bloom += light.sample(s, in.uv + float2(x, y) * pixel * 3).rgb;
    value += bloom * 0.035;
    float3 color = 1 - exp(-value * 1.4);
    float vignette = 1 - 0.35 * dot(in.uv - 0.5, in.uv - 0.5);
    color = pow(max(color, float3(0)), float3(0.82)) * vignette;
    return float4(color + float3(0.006, 0.009, 0.018), 1);
}

fragment float4 present(Quad in [[stage_in]], texture2d<float> image [[texture(0)]]) {
    constexpr sampler s(coord::normalized, address::clamp_to_edge, filter::linear);
    return image.sample(s, in.uv);
}

kernel void sample_words(const device uint *particles [[buffer(0)]],
                         device uint *result [[buffer(1)]],
                         constant uint &count [[buffer(2)]],
                         uint id [[thread_position_in_grid]]) {
    if (id >= 12) return;
    uint particle = id < 4 ? 0 : (id < 8 ? count / 2 : count - 1);
    result[id] = particles[particle * 4 + id % 4];
}
