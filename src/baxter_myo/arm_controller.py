import sys
from copy import deepcopy
import time
import math

import rospy
import tf
from std_msgs.msg import Header
from geometry_msgs.msg import (
    PoseStamped,
    Pose,
    Point,
    Twist,
    Quaternion,
)

import baxter_interface
from baxter_core_msgs.srv import (
    SolvePositionIK,
    SolvePositionIKRequest,
)

from baxter_myo.msg import MyoData

class ArmController(object):

    def __init__(self, limb, starting_pos=None, push_thresh=100,
                 mode='positions', rgbd=False):
        self.neutral_pos = starting_pos
        self.push_thresh = push_thresh
        self.gripper_enabled = False
        self.calibrated = True
        self.enabled = True
        self._closed_gripper = True
        self.rgbd = True # todo
        self.data = Twist()
        self.mode = mode
        rospy.init_node("baxter_myo")

        if self.mode == "positions":
            rospy.Subscriber("myo_data", MyoData, self.callback)
            rospy.loginfo("Subscribed to myo_data")
            self.listener = tf.TransformListener()
            if self.rgbd:
                s = raw_input("Calibrated: ")
                if s == "y":
                    (trans,rot) = self.listener.lookupTransform('/openni_link', '/left_hand_1', rospy.Time(0))
                    print "Recording: " + str(trans)
                    self.initial_pos = trans
                    self.previous_pos = [0,0,0]
                else:
                    return
        else:
            rospy.Subscriber("myo_data_high", MyoData, self.high_callback)
            rospy.loginfo("Subscribed to myo_data_high")
            rospy.Subscriber("myo_data_low", MyoData, self.low_callback)
            rospy.loginfo("Subscribed to myo_data_low")
            self.high_received = False
            self.low_received = False
            self.high_calibrated = True
            self.low_calibrated = True
            self.high_enabled = True
            self.low_enabled = True
            self.high_data = Twist()
            self.low_data = Twist()
            self.new_poss = deepcopy(self.neutral_pos)

        self.name_limb = limb
        self._limb = baxter_interface.Limb(self.name_limb)
        rospy.loginfo("Enabling Baxter")
        self._rs = baxter_interface.RobotEnable(baxter_interface.CHECK_VERSION)
        self._rs.enable()
        self._gripper = baxter_interface.Gripper(self.name_limb)
        self._gripper.calibrate()
        # TODO add tucking

        self.received = True
        self.baxter_off = Twist()
        rospy.loginfo("Moving to neutral position")
        self.move_to_neutral()
        self.initial_end_pose = {}
        rospy.loginfo("Recording offset")
        self.set_offset()

    def callback(self, data):
        self.received = True
        self.gripper_enabled = data.gripper
        self.calibrated = data.calibrated
        self.enabled = data.enabled
        self.data = deepcopy(data.data)

    def high_callback(self, data):
        self.high_received = True
        # self.high_calibrated = data.calibrated
        # self.high_enabled = data.enabled TODO
        self.high_data = deepcopy(data.data)

    def low_callback(self, data):
        self.low_received = True
        self.gripper_enabled = data.gripper
        self.low_calibrated = data.calibrated
        self.low_enabled = data.enabled
        self.low_data = deepcopy(data.data)

    def move_to_neutral(self):
        self._limb.move_to_joint_positions(self.neutral_pos)

    def set_offset(self):
        pose = self._limb.endpoint_pose()
        self.initial_end_pose = pose
        eu = tf.transformations.euler_from_quaternion(pose['orientation'])
        self.baxter_off.linear.x = pose['position'][0]
        self.baxter_off.linear.y = pose['position'][1]
        self.baxter_off.linear.z = pose['position'][2]
        self.baxter_off.angular.x = eu[0]
        self.baxter_off.angular.y = eu[1]
        self.baxter_off.angular.z = eu[2]

    def get_effort(self):
        e = self._limb.joint_efforts()
        s = sum([abs(e[i]) for i in e.keys()])
        return s

    def is_pushing(self):
        e = self.get_effort()
        return e > self.push_thresh


    def find_joint_position(self, pose, x_off=0.0, y_off=0.0, z_off=0.0):
        '''
        Finds the joint position of the arm given some pose and the
        offsets from it (to avoid opening the structure all the time
        outside of the function).
        '''
        ik_srv = "ExternalTools/right/PositionKinematicsNode/IKService"
        iksvc = rospy.ServiceProxy(ik_srv, SolvePositionIK)
        ik_request = SolvePositionIKRequest()
        the_pose = deepcopy(pose)
        the_pose['position'] = Point(x=pose['position'].x + x_off,
                                     y=pose['position'].y + y_off,
                                     z=pose['position'].z + z_off)
        approach_pose = Pose()
        approach_pose.position = the_pose['position']
        approach_pose.orientation = the_pose['orientation']
        hdr = Header(stamp=rospy.Time.now(), frame_id='base')
        pose_req = PoseStamped(header=hdr, pose=approach_pose)
        ik_request.pose_stamp.append(pose_req)
        resp = iksvc(ik_request)
        return dict(zip(resp.joints[0].name, resp.joints[0].position))

    def find_joint_pose(self, pose, targ_x=0.0, targ_y=0.0, targ_z=0.0,
                        targ_ox=0.0, targ_oy=0.0, targ_oz=0.0):
        '''
        WRITE_ME
        '''
        ik_srv = "ExternalTools/right/PositionKinematicsNode/IKService"
        iksvc = rospy.ServiceProxy(ik_srv, SolvePositionIK)
        ik_request = SolvePositionIKRequest()
        the_pose = deepcopy(pose)
        the_pose['position'] = Point(x=targ_x + self.baxter_off.linear.x,
                                     y=targ_y + self.baxter_off.linear.y,
                                     z=targ_z + self.baxter_off.linear.z)
        angles = tf.transformations.quaternion_from_euler(
            targ_ox + self.baxter_off.angular.x,
            targ_oy + self.baxter_off.angular.y,
            targ_oz + self.baxter_off.angular.z)
        the_pose['orientation'] = Quaternion(x=angles[0],
                                             y=angles[1],
                                             z=angles[2],
                                             w=angles[3])
        approach_pose = Pose()
        approach_pose.position = the_pose['position']
        approach_pose.orientation = the_pose['orientation']
        hdr = Header(stamp=rospy.Time.now(), frame_id='base')
        pose_req = PoseStamped(header=hdr, pose=approach_pose)
        ik_request.pose_stamp.append(pose_req)
        try:
            resp = iksvc(ik_request)
            return dict(zip(resp.joints[0].name, resp.joints[0].position))
        except:
            return None

    def step(self):
        if self.mode == "positions":
            self.step_pos()
        else:
            self.step_angles()

    def step_pos(self):
        if not self.enabled:
            print "Not enabled!"
            return None
        if not self.calibrated:
            print "Not calibrated!"
            rospy.loginfo("Moving to initial position")
            self.move_to_neutral()
        elif self.received:
            if self.rgbd:
                offset = self.calculate_pose()
                # print offset
                ofx = offset[0]
                ofy = offset[1]
                ofz = offset[2]
            else:
                ofx=float(self.data.linear.x)
                ofy=float(self.data.linear.y)
                ofz=float(self.data.linear.z)

            new_poss = self.find_joint_pose(
                self._limb.endpoint_pose(),
                targ_x=ofx,
                targ_y=ofy,
                targ_z=ofz,
                targ_ox=float(self.data.angular.x),
                targ_oy=float(self.data.angular.y),
                targ_oz=float(self.data.angular.z))
            rospy.loginfo("Moving to new position")
            if new_poss is not None:
                self._limb.move_to_joint_positions(new_poss,
                                                   timeout=0.1)
            else:
                rospy.loginfo("Cannot move to this position!")
            self.received = True
        if self._gripper.gripping():
            rospy.loginfo("Gripping!")
        if self.gripper_enabled:
            if not self._closed_gripper:
                rospy.loginfo("Closing gripper")
                self._closed_gripper = True
            self._gripper.close()
        else:
            if self._closed_gripper:
                rospy.loginfo("Opening gripper")
                self._closed_gripper = False
            self._gripper.open()

    def calculate_pose(self):
            try:
                (trans,rot) = self.listener.lookupTransform('/openni_link', '/left_hand_1', rospy.Time(0))
            except (tf.LookupException, tf.ConnectivityException, tf.ExtrapolationException):
                return [0, 0, 0]
            # print "Initial pos: " + str(self.initial_pos)
            # print "Current translation: " + str(trans)
            l = zip(self.initial_pos, trans)
            rgbd_diff = [x - y for (x, y) in l]
            # print "RGBD position: " + str(rgbd_diff) # v

            # CALC C_0
            # c_0 = self.limb.endpoint_pose() - self.initial_end_pose
            c_0 = []
            cp = self._limb.endpoint_pose()['position']
            ip = self.initial_end_pose['position']
            # print cp
            # print ip
            c_0.append(cp.x - ip.x)
            c_0.append(cp.y - ip.y)
            c_0.append(cp.z - ip.z)

            rgbd_diff[2] = rgbd_diff[2]
            d = zip(rgbd_diff, c_0)
            diff = [x - y for (x, y) in d]
            # print "Previous pos: " + str(c_0)
            diff[2] = -diff[2]
            # print "Differential: " + str(diff)
            # raw_input("Press enter")
            # self.previous_pos = rgbd_diff
            return diff


    def step_angles(self):
        if not (self.high_enabled and self.low_enabled):
            return None
        if not (self.high_calibrated and self.low_calibrated):
            rospy.loginfo("Moving to initial position")
            self.move_to_neutral()
        elif self.high_received or self.low_received:
            flag = self.set_angles()
            if not flag:
                rospy.loginfo("Cannot move to this position!")
            self.received = False
        if self._gripper.gripping():
            rospy.loginfo("Gripping!")
        if self.gripper_enabled:
            if not self._closed_gripper:
                rospy.loginfo("Closing gripper")
                self._closed_gripper = True
            self._gripper.close()
        else:
            if self._closed_gripper:
                rospy.loginfo("Opening gripper")
                self._closed_gripper = False
            self._gripper.open()

    def set_angles(self):
        """
        Set angles given data from two Myos.
        Returns True if angles can be set, otherwise returns False
        """
        limb_name = self.name_limb
        if self.high_received:
            e0 = math.radians(self.high_data.angular.x)
            s1 = math.radians(self.high_data.angular.y)
            s0 = math.radians(self.high_data.angular.z)
            self.new_poss[limb_name + '_e0'] = -e0
            self.new_poss[limb_name + '_s1'] = s1
            self.new_poss[limb_name + '_s0'] = s0

        if self.low_received:
            w0 = math.radians(self.low_data.angular.x)
            e1 = math.radians(self.low_data.angular.y)
            w1 = math.radians(self.low_data.angular.z)
            self.new_poss[limb_name + '_w0'] = -w0
            self.new_poss[limb_name + '_e1'] = e1
            self.new_poss[limb_name + '_w1'] = w1
        self._limb.move_to_joint_positions(self.new_poss, timeout=0.2)
        return True

def main():
    ac = ArmController('right')
    while 1:
        ac.step()

if __name__ == "__main__":
    sys.exit(main())
