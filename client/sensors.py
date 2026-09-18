"""传感器封装: LIS2DH12(三轴)/Ath20(温湿)/gl5528(光敏)/LBS(基站定位).

复用 usr/libs 已验证的驱动, 统一接口方便采集.
"""
from machine import I2C
from misc import ADC
import utime as time

# 复用 usr 目录下已验证的驱动
from usr.libs.LIS2DH12 import lis2dh12
from usr.libs.Ath20 import Aht20
from usr.libs.gl5528 import Gl5528


class AccelSensor:
    """三轴加速度计(LIS2DH12)封装. 用途: 跌倒检测."""
    def __init__(self, i2c_port=I2C.I2C1, mode=2, int_pin=0):
        """
        mode=2: 低功耗模式(已在 sz.py 验证)
        int_pin: 中断引脚(不使用中断则为0)
        """
        i2c_dev = I2C(i2c_port, I2C.STANDARD_MODE)
        self.dev = lis2dh12(i2c_dev, int_pin)
        self.dev.start_sensor()
        self.dev.set_mode(mode)  # 2 低功耗

    def read(self):
        """返回 (x, y, z) 单位 g."""
        return self.dev.read_acceleration

    def magnitude(self):
        """合成加速度 |a| = sqrt(x^2 + y^2 + z^2)."""
        import math
        x, y, z = self.read()
        return math.sqrt(x*x + y*y + z*z)


class TempHumSensor:
    """温湿度(Aht20)封装."""
    def __init__(self, addr=0x38):
        self.dev = Aht20(addr)

    def read(self):
        """返回 (humidity %, temperature C)."""
        return self.dev.read()


class LightSensor:
    """光敏(GL5528)封装."""
    def __init__(self, adc_ch=ADC.ADC0):
        adc_dev = ADC()
        self.dev = Gl5528(adc_dev, adc_ch)

    def read(self):
        """返回 (resistance Ω, lux)."""
        return self.dev.read()


class LbsLocator:
    """LBS基站定位复用已验证的地址解析."""
    def __init__(self):
        pass

    def get_lbs_data(self):
        """获取原始LBS数据, 后端可调用逆查经纬度."""
        import net
        cell_info = net.getCellInfo()
        if cell_info != -1 and cell_info[2]:
            first_tuple = cell_info[2]
            # 按照 QthSDK 格式 $LBS,MCC,MNC,LAC,CID,TAC,0*校验和;
            mcc = first_tuple[0][2]
            mnc = first_tuple[0][3]
            lac = first_tuple[0][5]
            cid = first_tuple[0][1]
            tac = first_tuple[0][7]
            lbs_str = "$LBS,{},{},{},{},{},0*69;".format(mcc, mnc, lac, cid, tac)
            return {"mcc": mcc, "mnc": mnc, "lac": lac, "cid": cid, "tac": tac, "raw": lbs_str}
        return None

# 单例使用示例(若多设备这里调整构造参数即可):
# accel = AccelSensor()
# th = TempHumSensor()
# light = LightSensor()
# lbs = LbsLocator()
