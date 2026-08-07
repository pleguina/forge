#ifndef LATENCY_SHAPER_H
#define LATENCY_SHAPER_H

#include <cstddef>
#include <deque>

namespace verif_fw {

template<typename T>
class LatencyShaper {
    std::deque<T> fifo_;
    int latency_;

public:
    LatencyShaper(int latency) : latency_(latency) {}

    void push(const T& val) {
        fifo_.push_back(val);
    }

    T pop() {
        if (fifo_.size() > (size_t)latency_) {
            T result = fifo_.front();
            fifo_.pop_front();
            return result;
        }
        return T();  // Return default-constructed (invalid)
    }

    int size() const {
        return fifo_.size();
    }
};

} // namespace verif_fw

#endif // LATENCY_SHAPER_H
