#!/usr/bin/env python3
from __future__ import print_function

# Change python2 to python3 above if you want to use PyTorch!


##### add python path #####
import sys
import os
import rospkg
import rospy

PATH = rospkg.RosPack().get_path("sim2real") + "/scripts"
print(PATH)
sys.path.append(PATH)


import gym
import env
import numpy as np
from collections import deque
import json
import random
import math
import yaml
import time
from sim2real.msg import Result, Query
from sklearn.utils import shuffle
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from joblib import dump, load

import torch
import torch.nn as nn
import torch.nn.functional as F

project_path = rospkg.RosPack().get_path("sim2real")
yaml_file = project_path + "/config/eval3.yaml"

######################################## PLEASE CHANGE TEAM NAME ########################################
TEAM_NAME = "ROBOLUCK"
######################################## PLEASE CHANGE TEAM NAME ########################################
team_path = project_path + "/project/IS_" + TEAM_NAME

class Model(nn.Module):
    def __init__(self, input_dim, hidden_dim1, hidden_dim2, hidden_dim3):
        super(Model, self).__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim1)
        self.fc2 = nn.Linear(hidden_dim1, hidden_dim2)
        self.fc3 = nn.Linear(hidden_dim2, hidden_dim3)
        self.fc4 = nn.Linear(hidden_dim3, 1)
    def forward(self, x):
        x = self.fc1(x)
        x = F.tanh(x)
        x = self.fc2(x)
        x = F.relu(x)
        x = self.fc3(x)
        x = F.relu(x)
        x = self.fc4(x)
        return x

class GaussianProcess:
    def __init__(self, args):
        rospy.init_node('gaussian_process_' + TEAM_NAME, anonymous=True, disable_signals=True)
        # env reset with world file
        self.env = gym.make('RCCar-v0')
        self.env.seed(1)
        self.env.unwrapped
        self.env.load(args['world_name'])
        self.track_list = self.env.track_list
        
        self.time_limit = 150.0
        
        """
        add your demonstration files with expert state-action pairs.
        you can collect expert demonstration using pure pursuit.
        you can define additional class member variables.
        """
        #DON'T CHANGE THIS PART!
        # 1.5 <= minVel <= maxVel <= 3.0
        self.maxAng = 1.5
        self.minVel = 0.5
        self.maxVel = 3.0
        ########################
        
        self.demo_files = []

        self.obs_num = 1000

        self.demo_obs = []
        self.demo_act = []

        self.kernel = 0

        
        self.gp = GaussianProcessRegressor(kernel = self.kernel, alpha = 0.2)
        self.gp_file_name = "demo"
        self.gp_file = project_path + "/" + self.gp_file_name + ".joblib"

        self.load()

        self.query_sub = rospy.Subscriber("/query", Query, self.callback_query)
        self.rt_pub = rospy.Publisher("/result", Result, queue_size = 1)

        print("completed initialization")


    def load(self):
        """
        1) load your expert demonstration
        2) normalization and gp fitting (recommand using scikit-learn package)
        3) if you already have a fitted model, load it instead of fitting the model again.
        Please implement loading pretrained model part for fast evaluation.
        """
        self.means = torch.load(project_path + '/project/IS_ROBOLUCK/project/means_cpu.pt')
        self.stds = torch.load(project_path + '/project/IS_ROBOLUCK/project/stds_cpu.pt')
        self.model = torch.load(project_path + '/project/IS_ROBOLUCK/project/model_cpu.pt')

    def get_action(self, obs):
        """
        1) input observation is the raw data from environment.
        2) 0 to 1080 components (totally 1081) are from lidar.
           Also, 1081 component and 1082 component are scaled velocity and steering angle, respectively.
        3) To improve your algorithm, you must process the raw data so that the model fits well.
        4) Always keep in mind of normalization process during implementation.
        """
        obs = torch.from_numpy(obs).float()
        processed_obs = torch.nan_to_num(obs, posinf=11.0)
        norm_obs = (processed_obs - self.means) / self.stds
        steer = self.model(norm_obs[:, :-2])
        steer = steer.detach().numpy()
        steer = steer[0, 0] * self.stds[-1] + self.means[-1]
        act = np.array([[steer, 3.0 - np.abs(steer)*4/3]])
        return act    

    def callback_query(self, data):
        rt = Result()
        START_TIME = rospy.get_time()
        is_exit = data.exit
        try:
            # if query is valid, start
            if data.name != TEAM_NAME:
                return
            
            if data.world not in self.track_list:
                END_TIME = rospy.get_time()
                rt.id = data.id
                rt.trial = data.trial
                rt.team = data.name
                rt.world = data.world
                rt.elapsed_time = END_TIME - START_TIME
                rt.waypoints = 0
                rt.n_waypoints = 20
                rt.success = False
                rt.fail_type = "Invalid Track"
                self.rt_pub.publish(rt)
                return
            
            print("[%s] START TO EVALUATE! MAP NAME: %s" %(data.name, data.world))
            obs = self.env.reset(name = data.world)
            obs = np.reshape(obs, [1,-1])
            
            
            while True:
                if rospy.get_time() - START_TIME > self.time_limit:
                    END_TIME = rospy.get_time()
                    rt.id = data.id
                    rt.trial = data.trial
                    rt.team = data.name
                    rt.world = data.world
                    rt.elapsed_time = END_TIME - START_TIME
                    rt.waypoints = self.env.next_checkpoint
                    rt.n_waypoints = 20
                    rt.success = False
                    rt.fail_type = "Exceed Time Limit"
                    self.rt_pub.publish(rt)
                    print("EXCEED TIME LIMIT")
                    break
                
                act = self.get_action(obs)
                input_steering = np.clip(act[0][0], -self.maxAng, self.maxAng)
                input_velocity = np.clip(act[0][1], self.minVel, self.maxVel)
                obs, _, done, logs = self.env.step([input_steering, input_velocity])
                obs = np.reshape(obs, [1,-1])
                
                if done:
                    END_TIME = rospy.get_time()
                    rt.id = data.id
                    rt.trial = data.trial
                    rt.team = data.name
                    rt.world = data.world
                    rt.elapsed_time = END_TIME - START_TIME
                    rt.waypoints = logs['checkpoints']
                    rt.n_waypoints = 20
                    rt.success = True if logs['info'] == 3 else False
                    rt.fail_type = ""
                    print(logs)
                    if logs['info'] == 1:
                        rt.fail_type = "Collision"
                    if logs['info'] == 2:
                        rt.fail_type = "Exceed Time Limit"
                    self.rt_pub.publish(rt)
                    print("publish result")
                    break
        
        except Exception as e:
            print(e)
            END_TIME = rospy.get_time()
            rt.id = data.id
            rt.trial = data.trial
            rt.team = data.name
            rt.world = data.world
            rt.elapsed_time = END_TIME - START_TIME
            rt.waypoints = 0
            rt.n_waypoints = 20
            rt.success = False
            rt.fail_type = "Script Error"
            self.rt_pub.publish(rt)

        if is_exit:
            rospy.signal_shutdown("End query")
        
        return

if __name__ == '__main__':
    with open(yaml_file) as file:
        args = yaml.load(file)
    GaussianProcess(args)
    rospy.spin()

