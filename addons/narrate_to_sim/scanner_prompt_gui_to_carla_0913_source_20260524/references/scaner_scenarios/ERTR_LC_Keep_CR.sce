<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<sce version="20.25.2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">

  <Scenario>
    <description></description>
    <name></name>
    <steadystate>0</steadystate>
    <CustomDataExt/>
    <Environment>
      <gravity>9.81</gravity>
      <waterVolumetricMass>1000</waterVolumetricMass>
      <DayTime>
        <dawnTime>21600</dawnTime>
        <day>1</day>
        <duskTime>72000</duskTime>
        <month>6</month>
        <startTime>43200</startTime>
        <sunAzimuth>0</sunAzimuth>
        <sunElevation>0</sunElevation>
        <sunPositionEnable>0</sunPositionEnable>
        <timeFactor>0</timeFactor>
        <year>2000</year>
      </DayTime>
      <Lighting>
        <dynamicLighting>1</dynamicLighting>
        <lightMax>1</lightMax>
        <lightMin>0</lightMin>
      </Lighting>
      <Panorama>
        <file></file>
        <scale>0.00225</scale>
      </Panorama>
      <Weather>
        <airTemperature>293.15</airTemperature>
        <atmosphericPressure>101500</atmosphericPressure>
        <autoComputeFogColor>1</autoComputeFogColor>
        <autoComputeFogDistance>1</autoComputeFogDistance>
        <autoComputeSideEffects>0</autoComputeSideEffects>
        <cloudsLevel>0.2</cloudsLevel>
        <fogColor>0.5 0.5 0.5</fogColor>
        <fogDistance>20000</fogDistance>
        <fogEnable>1</fogEnable>
        <groundTemperature>293.15</groundTemperature>
        <hygrometry>0.5</hygrometry>
        <rainLevel>0</rainLevel>
        <skySaturation>1</skySaturation>
        <snowLevel>0</snowLevel>
        <snowOnRoad>0</snowOnRoad>
        <waterOnRoad>0</waterOnRoad>
      </Weather>
    </Environment>
    <ExternalTraffic>
      <extTrafficConfigFile></extTrafficConfigFile>
      <replication>0</replication>
    </ExternalTraffic>
    <ExternalTrafficConnexion>
      <host></host>
      <port>0</port>
    </ExternalTrafficConnexion>
    <Ground>
      <enableRoughness>0</enableRoughness>
      <infrastructure></infrastructure>
      <isParametric>0</isParametric>
      <name>Straight_highway_3_3.rnd</name>
      <useSpecificSurfaceForTraffic>0</useSpecificSurfaceForTraffic>
    </Ground>
    <PredefinedTest>
      <type>0</type>
    </PredefinedTest>
    <ProgressDlg>
      <show3dView>0</show3dView>
      <showGraphs>0</showGraphs>
      <ResultsGraph>
        <abcissa>time</abcissa>
        <abcissaUnit>s</abcissaUnit>
        <tabName>Graph</tabName>
        <title>Graph</title>
      </ResultsGraph>
    </ProgressDlg>
    <ProjectInfos>
      <designOfExperimentName></designOfExperimentName>
      <explorationPath></explorationPath>
      <originalName></originalName>
      <testCaseId></testCaseId>
      <testPlanName></testPlanName>
      <testPlanPath></testPlanPath>
    </ProjectInfos>
    <ScenarioStopCriteria>
      <finalTime>3600</finalTime>
      <stopAtFinalTime>0</stopAtFinalTime>
    </ScenarioStopCriteria>
    <StoryBoard>
      <firstStep>Step</firstStep>
      <openScenario></openScenario>
      <Step>
        <description></description>
        <hint></hint>
        <name>_background_task_step</name>
        <ScenarioScript>
          <isScriptActive>1</isScriptActive>
          <script>
TYPEOF SCENARIO (0)
{
	LABEL="Script_0"
	FILE="Agent1_Type1/ERTR_LC_Keep_CR_Script_0"
	TASK_KIND="0"
}
</script>
          <scriptLanguage>mice</scriptLanguage>
          <scriptName>Script_0</scriptName>
        </ScenarioScript>
      </Step>
      <Transition>
        <allConditionsTrue>0</allConditionsTrue>
        <disabled>0</disabled>
        <fromStep>_background_task_step</fromStep>
        <name>_end_scenario_condition_list</name>
        <toStep>BUILTIN_SUCCESS_ADB568D064DB408EAFDE1D4021E336DF</toStep>
      </Transition>
      <Transition>
        <allConditionsTrue>0</allConditionsTrue>
        <disabled>0</disabled>
        <fromStep>_background_task_step</fromStep>
        <name>_failed_scenario_condition_list</name>
        <toStep>BUILTIN_FAILURE_A36CBB59761A4AF1B2BACC50D71084AB</toStep>
      </Transition>
      <Step>
        <description></description>
        <hint></hint>
        <name>Step</name>
        <ScenarioScript>
          <isScriptActive>1</isScriptActive>
          <script>
TYPEOF SCENARIO (0)
{
	IS_EXPANDED="1"
	LABEL="Script_0"
	FILE="Agent1_Type1/ERTR_LC_Keep_CR_Script_0"
	TASK_KIND="0"

	TYPEOF RULE (1)
	{
		IS_EXPANDED="1"
		LABEL="Rule"

		TYPEOF CONDITION (IS_SCENARIO_BEGINNING)
		{
			IS_EXPANDED="1"
		}

		TYPEOF ACTION (SOUND_LOAD)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (MESSAGE)
			{
				KIND="FILE"
				VALUE="NormalDriveTest_400.wav"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SOUND_NUMBER)
			{
				KIND="USER"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SOUND_3DTYPE)
			{
				KIND="ENUM"
				VALUE="SOUND_3D_TYPE_SINGLE"
				OWNER_FILE=""
			}
		}
	}

	TYPEOF RULE (2)
	{
		IS_EXPANDED="1"
		LABEL="Rule"

		TYPEOF CONDITION (IS_TRIGGERED_BY_VEHICLE)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (TRIGGER_NAME)
			{
				KIND="TRIGGER"
				VALUE="Trigger 1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (GO_TO_LEFT_LANE)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (LANE_CHANGE_RISK)
			{
				KIND="USER"
				VALUE="0.5"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (DURATION)
			{
				KIND="USER"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_SPEED_TARGET)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="120"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (STATE)
			{
				KIND="ENUM"
				VALUE="STATE_ON"
				OWNER_FILE=""
			}
		}
	}

	TYPEOF RULE (0)
	{
		IS_EXPANDED="1"
		LABEL="Rule"

		TYPEOF CONDITION (IS_TRIGGERED_BY_VEHICLE)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (TRIGGER_NAME)
			{
				KIND="TRIGGER"
				VALUE="Trigger"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_VHL_MAX_SPEED)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="120"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_VHL_MAX_SPEED)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="2"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="60"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_VHL_MAX_SPEED)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="3"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="60"
				OWNER_FILE=""
			}
		}
	}

	TYPEOF RULE (3)
	{
		IS_EXPANDED="1"
		LABEL="Rule"

		TYPEOF CONDITION (IS_TRIGGERED_BY_VEHICLE)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (TRIGGER_NAME)
			{
				KIND="TRIGGER"
				VALUE="Trigger 3"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_VHL_MAX_SPEED)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SET_SPEED_TARGET)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SPEED)
			{
				KIND="USER"
				VALUE="0"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (STATE)
			{
				KIND="ENUM"
				VALUE="STATE_ON"
				OWNER_FILE=""
			}
		}
	}

	TYPEOF RULE (4)
	{
		IS_EXPANDED="1"
		LABEL="Rule"

		TYPEOF CONDITION (IS_TRIGGERED_BY_VEHICLE)
		{
			IS_EXPANDED="1"
			MODIFIER="BECOMES_TRUE"

			TYPEOF PARAMETER (TRIGGER_NAME)
			{
				KIND="TRIGGER"
				VALUE="Trigger 2"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (VHL_NUMBER)
			{
				KIND="VEHICLE"
				VALUE="0"
				OWNER_FILE=""
			}
		}

		TYPEOF ACTION (SOUND_PLAY)
		{
			IS_EXPANDED="1"

			TYPEOF PARAMETER (SOUND_NUMBER)
			{
				KIND="USER"
				VALUE="1"
				OWNER_FILE=""
			}

			TYPEOF PARAMETER (SOUND_PLAYMODE)
			{
				KIND="ENUM"
				VALUE="SOUND_PLAYMODE_ONCE"
				OWNER_FILE=""
			}
		}
	}
}
</script>
          <scriptLanguage>mice</scriptLanguage>
          <scriptName>Script_0</scriptName>
        </ScenarioScript>
      </Step>
      <Transition>
        <allConditionsTrue>1</allConditionsTrue>
        <disabled>0</disabled>
        <fromStep>Step</fromStep>
        <name>Forward</name>
        <toStep>BUILTIN_SUCCESS_ADB568D064DB408EAFDE1D4021E336DF</toStep>
      </Transition>
      <Transition>
        <allConditionsTrue>0</allConditionsTrue>
        <disabled>0</disabled>
        <fromStep>Step</fromStep>
        <name>Failure</name>
        <toStep>BUILTIN_FAILURE_A36CBB59761A4AF1B2BACC50D71084AB</toStep>
      </Transition>
    </StoryBoard>
    <UserChannels/>
    <UserDataList/>
    <Visual>
      <Others>
        <loadCache>0</loadCache>
        <optimize>1</optimize>
        <refreshRate>60</refreshRate>
        <viewMode></viewMode>
      </Others>
      <Quality>
        <anisotropicFiltering>8</anisotropicFiltering>
        <antiAliasing>8</antiAliasing>
        <fadeLOD>0</fadeLOD>
      </Quality>
      <RoadMarks>
        <mode></mode>
        <nodeName></nodeName>
        <Interval>
          <max>0</max>
          <min>0</min>
        </Interval>
      </RoadMarks>
      <Sfx>
        <dazzleHalo>1</dazzleHalo>
        <fog>1</fog>
        <fogHalo>1</fogHalo>
        <sky>1</sky>
      </Sfx>
      <Viewing>
        <farPlane>5000</farPlane>
        <nearPlane>1</nearPlane>
        <terrainLODScale>1</terrainLODScale>
        <vehicleLODScale>1</vehicleLODScale>
      </Viewing>
    </Visual>
    <Vehicle>
      <cabin></cabin>
      <decorationName></decorationName>
      <driveInReverse>0</driveInReverse>
      <driverId>0</driverId>
      <id>0</id>
      <initEngineRunning>1</initEngineRunning>
      <initialDistOnTrajectory>0</initialDistOnTrajectory>
      <initialSpeed>0</initialSpeed>
      <modelColor>0 0 0</modelColor>
      <modelName>ExecutiveCar</modelName>
      <motionId>-1</motionId>
      <name>ExecutiveCar</name>
      <process>${DIGITALVEHICLE}</process>
      <recomputeRestitutionMovement>1</recomputeRestitutionMovement>
      <state>0</state>
      <trailerId>-1</trailerId>
      <versionName></versionName>
      <ItineraryRoadXml>
        <endBehaviour>0</endBehaviour>
      </ItineraryRoadXml>
      <Model>
        <Callas>
          <activeConfiguration>0</activeConfiguration>
          <antiWheeling>0</antiWheeling>
          <applyGroundBodyInteractionForces>0</applyGroundBodyInteractionForces>
          <autoClutch>1</autoClutch>
          <brakeTemperature>313.15</brakeTemperature>
          <computeGroundBodyInteraction>0</computeGroundBodyInteraction>
          <computeGroundCanonInteraction>0</computeGroundCanonInteraction>
          <corneringStartType>0</corneringStartType>
          <cutThrottleOnGearChange>1</cutThrottleOnGearChange>
          <damperTemperature>313.15</damperTemperature>
          <delayShiftDownAfterDown>0.5</delayShiftDownAfterDown>
          <delayShiftDownAfterUp>0.5</delayShiftDownAfterUp>
          <delayShiftUpAfterDown>0.5</delayShiftUpAfterDown>
          <delayShiftUpAfterUp>0.5</delayShiftUpAfterUp>
          <detailedRuts>0</detailedRuts>
          <gearboxTemperature>373.15</gearboxTemperature>
          <initBatteryCharge>1</initBatteryCharge>
          <initialLateralAcceleration>0</initialLateralAcceleration>
          <initialSteeringAngle>0</initialSteeringAngle>
          <initialStepIncrement>0.0005</initialStepIncrement>
          <initialTurnRadius>0</initialTurnRadius>
          <isDelayShiftDownAfterDown>1</isDelayShiftDownAfterDown>
          <isDelayShiftDownAfterUp>1</isDelayShiftDownAfterUp>
          <isDelayShiftUpAfterDown>1</isDelayShiftUpAfterDown>
          <isDelayShiftUpAfterUp>1</isDelayShiftUpAfterUp>
          <isLockGearDuringCornering>0</isLockGearDuringCornering>
          <isParallelComputation>0</isParallelComputation>
          <limitManualGearChoice>0</limitManualGearChoice>
          <lockGearDuringCorneringAngle>3.14159265358979</lockGearDuringCorneringAngle>
          <modulesTimeout>30</modulesTimeout>
          <numericalMethod>1</numericalMethod>
          <oilTemperature>353.15</oilTemperature>
          <regulateBrakePedal>0</regulateBrakePedal>
          <staticStateComputation>0</staticStateComputation>
          <tireTemperature>313.15</tireTemperature>
          <toeHealOnDownshift>0</toeHealOnDownshift>
          <waterTemperature>353.15</waterTemperature>
          <CustomData/>
          <Hack>
            <useNewMovementEquations>1</useNewMovementEquations>
            <useNewWheelDOF>1</useNewWheelDOF>
          </Hack>
        </Callas>
      </Model>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2190 8.74999999999479 0</position>
        <RoadPosition>
          <abscissa>1810</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
      <StopCriteria>
        <finalDistance>100000</finalDistance>
        <finalTime>3600</finalTime>
        <maxSideslipAngle>0.261799387799149</maxSideslipAngle>
        <maxSideslipAngleSpeed>0.349065850398866</maxSideslipAngleSpeed>
        <maxSpeed>111.111111111111</maxSpeed>
        <maxTrajectoryError>3</maxTrajectoryError>
        <minSpeed>0.277777777777778</minSpeed>
        <stopCanonContact>0</stopCanonContact>
        <stopConeCollision>0</stopConeCollision>
        <stopFinalDistance>0</stopFinalDistance>
        <stopFinalTime>0</stopFinalTime>
        <stopLateralAccelerationPeek>0</stopLateralAccelerationPeek>
        <stopMaxSpeed>0</stopMaxSpeed>
        <stopMinSpeed>0</stopMinSpeed>
        <stopSideslipAngle>0</stopSideslipAngle>
        <stopSideslipAngleSpeed>0</stopSideslipAngleSpeed>
        <stopSpin>0</stopSpin>
        <stopStableMovement>0</stopStableMovement>
        <stopStall>0</stopStall>
        <stopStallDuration>1</stopStallDuration>
        <stopStandstill>0</stopStandstill>
        <stopTrajectoryError>0</stopTrajectoryError>
        <stopWheelLift>0</stopWheelLift>
      </StopCriteria>
      <Swarm>
        <appearBackRadius>250</appearBackRadius>
        <appearFrontRadius>500</appearFrontRadius>
        <density>1</density>
        <disappearBackRadius>500</disappearBackRadius>
        <disappearFrontRadius>1000</disappearFrontRadius>
        <frontAppearFactor>0.75</frontAppearFactor>
        <queen>-1</queen>
        <queenDirectionFactor>0.3</queenDirectionFactor>
      </Swarm>
      <Tag>
        <value>sc_entity_ego_A</value>
      </Tag>
    </Vehicle>
    <Driver>
      <driverName>Driver 0</driverName>
      <driverType>TrafficDriver</driverType>
      <id>0</id>
      <process></process>
      <HumanDriver>
        <ConfigurationFile></ConfigurationFile>
      </HumanDriver>
      <TrafficDriver>
        <accelCurveObserving>1</accelCurveObserving>
        <accelEmergencyPedestrianObserving>1</accelEmergencyPedestrianObserving>
        <accelEmergencyVehicleObserving>1</accelEmergencyVehicleObserving>
        <accelFollowObserving>1</accelFollowObserving>
        <accelRegulationObserving>1</accelRegulationObserving>
        <activateSharedDriving>1</activateSharedDriving>
        <autonomousToDriverTransitionTime>1</autonomousToDriverTransitionTime>
        <brakePedalThreashold>1</brakePedalThreashold>
        <disconectSteeringWhenAutonomous>1</disconectSteeringWhenAutonomous>
        <driverToAutonomousTransitionTime>5</driverToAutonomousTransitionTime>
        <gasPedalThreashold>0.01</gasPedalThreashold>
        <ignoreDriverPeriod>4</ignoreDriverPeriod>
        <laneBorderMargin>0.5</laneBorderMargin>
        <laneGapMean>0</laneGapMean>
        <laneGapPeriodMean>0</laneGapPeriodMean>
        <laneGapPeriodStdDev>0</laneGapPeriodStdDev>
        <laneGapStdDev>0.2</laneGapStdDev>
        <maxSpeed>27.7777777777778</maxSpeed>
        <norm>NormalDriver</norm>
        <overtakeRisk>0</overtakeRisk>
        <priorityObserving>1</priorityObserving>
        <safetyTime>3</safetyTime>
        <signObserving>1</signObserving>
        <speedLimitRisk>1</speedLimitRisk>
        <stayOnLane>1</stayOnLane>
        <steeringTorqueThreashold>5</steeringTorqueThreashold>
        <strategyFile>default</strategyFile>
      </TrafficDriver>
      <AirplaneDriver>
        <Trajectory>
          <isClosed>0</isClosed>
          <isWithObstacles>0</isWithObstacles>
          <notes></notes>
          <type>0</type>
          <CurvatureTrajectory>
            <initialHeading>0</initialHeading>
            <initialPosition>0 0</initialPosition>
          </CurvatureTrajectory>
          <XYTrajectory>
            <interpolationType>0</interpolationType>
          </XYTrajectory>
        </Trajectory>
      </AirplaneDriver>
    </Driver>
    <Vehicle>
      <cabin></cabin>
      <decorationName></decorationName>
      <driveInReverse>0</driveInReverse>
      <driverId>1</driverId>
      <id>1</id>
      <initEngineRunning>1</initEngineRunning>
      <initialDistOnTrajectory>0</initialDistOnTrajectory>
      <initialSpeed>0</initialSpeed>
      <modelColor>0 0 0</modelColor>
      <modelName>Citroen_C2</modelName>
      <motionId>-1</motionId>
      <name>Citroen_C2</name>
      <process>${TRAFFIC}</process>
      <recomputeRestitutionMovement>1</recomputeRestitutionMovement>
      <state>0</state>
      <trailerId>-1</trailerId>
      <versionName></versionName>
      <ItineraryRoadXml>
        <endBehaviour>0</endBehaviour>
      </ItineraryRoadXml>
      <Model>
        <Simple/>
      </Model>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2200.34790039062 8.74999999999476 0</position>
        <RoadPosition>
          <abscissa>1799.65209960938</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
      <StopCriteria>
        <finalDistance>100000</finalDistance>
        <finalTime>3600</finalTime>
        <maxSideslipAngle>0.261799387799149</maxSideslipAngle>
        <maxSideslipAngleSpeed>0.349065850398866</maxSideslipAngleSpeed>
        <maxSpeed>111.111111111111</maxSpeed>
        <maxTrajectoryError>3</maxTrajectoryError>
        <minSpeed>0.277777777777778</minSpeed>
        <stopCanonContact>0</stopCanonContact>
        <stopConeCollision>0</stopConeCollision>
        <stopFinalDistance>0</stopFinalDistance>
        <stopFinalTime>0</stopFinalTime>
        <stopLateralAccelerationPeek>0</stopLateralAccelerationPeek>
        <stopMaxSpeed>0</stopMaxSpeed>
        <stopMinSpeed>0</stopMinSpeed>
        <stopSideslipAngle>0</stopSideslipAngle>
        <stopSideslipAngleSpeed>0</stopSideslipAngleSpeed>
        <stopSpin>0</stopSpin>
        <stopStableMovement>0</stopStableMovement>
        <stopStall>0</stopStall>
        <stopStallDuration>1</stopStallDuration>
        <stopStandstill>0</stopStandstill>
        <stopTrajectoryError>0</stopTrajectoryError>
        <stopWheelLift>0</stopWheelLift>
      </StopCriteria>
      <Swarm>
        <appearBackRadius>250</appearBackRadius>
        <appearFrontRadius>500</appearFrontRadius>
        <density>1</density>
        <disappearBackRadius>500</disappearBackRadius>
        <disappearFrontRadius>1000</disappearFrontRadius>
        <frontAppearFactor>0.75</frontAppearFactor>
        <queen>-1</queen>
        <queenDirectionFactor>0.3</queenDirectionFactor>
      </Swarm>
    </Vehicle>
    <Driver>
      <driverName>Driver 1</driverName>
      <driverType>TrafficDriver</driverType>
      <id>1</id>
      <process></process>
      <TrafficDriver>
        <accelCurveObserving>1</accelCurveObserving>
        <accelEmergencyPedestrianObserving>1</accelEmergencyPedestrianObserving>
        <accelEmergencyVehicleObserving>1</accelEmergencyVehicleObserving>
        <accelFollowObserving>1</accelFollowObserving>
        <accelRegulationObserving>1</accelRegulationObserving>
        <activateSharedDriving>0</activateSharedDriving>
        <autonomousToDriverTransitionTime>1</autonomousToDriverTransitionTime>
        <brakePedalThreashold>1</brakePedalThreashold>
        <disconectSteeringWhenAutonomous>1</disconectSteeringWhenAutonomous>
        <driverToAutonomousTransitionTime>5</driverToAutonomousTransitionTime>
        <gasPedalThreashold>0.01</gasPedalThreashold>
        <ignoreDriverPeriod>4</ignoreDriverPeriod>
        <laneBorderMargin>0.5</laneBorderMargin>
        <laneGapMean>0</laneGapMean>
        <laneGapPeriodMean>0</laneGapPeriodMean>
        <laneGapPeriodStdDev>0</laneGapPeriodStdDev>
        <laneGapStdDev>0.2</laneGapStdDev>
        <maxSpeed>0</maxSpeed>
        <norm>NormalDriver</norm>
        <overtakeRisk>0</overtakeRisk>
        <priorityObserving>1</priorityObserving>
        <safetyTime>0.5</safetyTime>
        <signObserving>1</signObserving>
        <speedLimitRisk>1</speedLimitRisk>
        <stayOnLane>1</stayOnLane>
        <steeringTorqueThreashold>5</steeringTorqueThreashold>
        <strategyFile>default</strategyFile>
      </TrafficDriver>
      <AirplaneDriver>
        <Trajectory>
          <isClosed>0</isClosed>
          <isWithObstacles>0</isWithObstacles>
          <notes></notes>
          <type>0</type>
          <CurvatureTrajectory>
            <initialHeading>0</initialHeading>
            <initialPosition>0 0</initialPosition>
          </CurvatureTrajectory>
          <XYTrajectory>
            <interpolationType>0</interpolationType>
          </XYTrajectory>
        </Trajectory>
      </AirplaneDriver>
    </Driver>
    <Vehicle>
      <cabin></cabin>
      <decorationName></decorationName>
      <driveInReverse>0</driveInReverse>
      <driverId>2</driverId>
      <id>2</id>
      <initEngineRunning>1</initEngineRunning>
      <initialDistOnTrajectory>0</initialDistOnTrajectory>
      <initialSpeed>0</initialSpeed>
      <modelColor>0 0 0</modelColor>
      <modelName>Citroen_C3_Red</modelName>
      <motionId>-1</motionId>
      <name>Citroen_C3_Red</name>
      <process>${TRAFFIC}</process>
      <recomputeRestitutionMovement>1</recomputeRestitutionMovement>
      <state>0</state>
      <trailerId>-1</trailerId>
      <versionName></versionName>
      <ItineraryRoadXml>
        <endBehaviour>0</endBehaviour>
      </ItineraryRoadXml>
      <Model>
        <Simple/>
      </Model>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2176.99926757812 8.74999999999483 0</position>
        <RoadPosition>
          <abscissa>1823.00073242188</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
      <StopCriteria>
        <finalDistance>100000</finalDistance>
        <finalTime>3600</finalTime>
        <maxSideslipAngle>0.261799387799149</maxSideslipAngle>
        <maxSideslipAngleSpeed>0.349065850398866</maxSideslipAngleSpeed>
        <maxSpeed>111.111111111111</maxSpeed>
        <maxTrajectoryError>3</maxTrajectoryError>
        <minSpeed>0.277777777777778</minSpeed>
        <stopCanonContact>0</stopCanonContact>
        <stopConeCollision>0</stopConeCollision>
        <stopFinalDistance>0</stopFinalDistance>
        <stopFinalTime>0</stopFinalTime>
        <stopLateralAccelerationPeek>0</stopLateralAccelerationPeek>
        <stopMaxSpeed>0</stopMaxSpeed>
        <stopMinSpeed>0</stopMinSpeed>
        <stopSideslipAngle>0</stopSideslipAngle>
        <stopSideslipAngleSpeed>0</stopSideslipAngleSpeed>
        <stopSpin>0</stopSpin>
        <stopStableMovement>0</stopStableMovement>
        <stopStall>0</stopStall>
        <stopStallDuration>1</stopStallDuration>
        <stopStandstill>0</stopStandstill>
        <stopTrajectoryError>0</stopTrajectoryError>
        <stopWheelLift>0</stopWheelLift>
      </StopCriteria>
      <Swarm>
        <appearBackRadius>250</appearBackRadius>
        <appearFrontRadius>500</appearFrontRadius>
        <density>1</density>
        <disappearBackRadius>500</disappearBackRadius>
        <disappearFrontRadius>1000</disappearFrontRadius>
        <frontAppearFactor>0.75</frontAppearFactor>
        <queen>-1</queen>
        <queenDirectionFactor>0.3</queenDirectionFactor>
      </Swarm>
    </Vehicle>
    <Driver>
      <driverName>Driver 2</driverName>
      <driverType>TrafficDriver</driverType>
      <id>2</id>
      <process></process>
      <TrafficDriver>
        <accelCurveObserving>1</accelCurveObserving>
        <accelEmergencyPedestrianObserving>1</accelEmergencyPedestrianObserving>
        <accelEmergencyVehicleObserving>1</accelEmergencyVehicleObserving>
        <accelFollowObserving>1</accelFollowObserving>
        <accelRegulationObserving>1</accelRegulationObserving>
        <activateSharedDriving>0</activateSharedDriving>
        <autonomousToDriverTransitionTime>1</autonomousToDriverTransitionTime>
        <brakePedalThreashold>1</brakePedalThreashold>
        <disconectSteeringWhenAutonomous>1</disconectSteeringWhenAutonomous>
        <driverToAutonomousTransitionTime>5</driverToAutonomousTransitionTime>
        <gasPedalThreashold>0.01</gasPedalThreashold>
        <ignoreDriverPeriod>4</ignoreDriverPeriod>
        <laneBorderMargin>0.5</laneBorderMargin>
        <laneGapMean>0</laneGapMean>
        <laneGapPeriodMean>0</laneGapPeriodMean>
        <laneGapPeriodStdDev>0</laneGapPeriodStdDev>
        <laneGapStdDev>0.2</laneGapStdDev>
        <maxSpeed>0</maxSpeed>
        <norm>NormalDriver</norm>
        <overtakeRisk>0</overtakeRisk>
        <priorityObserving>1</priorityObserving>
        <safetyTime>2.5</safetyTime>
        <signObserving>1</signObserving>
        <speedLimitRisk>1</speedLimitRisk>
        <stayOnLane>1</stayOnLane>
        <steeringTorqueThreashold>5</steeringTorqueThreashold>
        <strategyFile>default</strategyFile>
      </TrafficDriver>
      <AirplaneDriver>
        <Trajectory>
          <isClosed>0</isClosed>
          <isWithObstacles>0</isWithObstacles>
          <notes></notes>
          <type>0</type>
          <CurvatureTrajectory>
            <initialHeading>0</initialHeading>
            <initialPosition>0 0</initialPosition>
          </CurvatureTrajectory>
          <XYTrajectory>
            <interpolationType>0</interpolationType>
          </XYTrajectory>
        </Trajectory>
      </AirplaneDriver>
    </Driver>
    <Trigger>
      <filter>*</filter>
      <height>2</height>
      <isActivated>1</isActivated>
      <isRectangle>0</isRectangle>
      <name>Trigger</name>
      <width>2</width>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2187.43969726562 8.7499999999948 0</position>
        <RoadPosition>
          <abscissa>1812.56030273438</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
    </Trigger>
    <Trigger>
      <filter>*</filter>
      <height>2</height>
      <isActivated>1</isActivated>
      <isRectangle>0</isRectangle>
      <name>Trigger 1</name>
      <width>2</width>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2127.02197265625 8.749999999995 0</position>
        <RoadPosition>
          <abscissa>1872.97802734375</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
    </Trigger>
    <Vehicle>
      <cabin></cabin>
      <decorationName></decorationName>
      <driveInReverse>0</driveInReverse>
      <driverId>3</driverId>
      <id>3</id>
      <initEngineRunning>1</initEngineRunning>
      <initialDistOnTrajectory>0</initialDistOnTrajectory>
      <initialSpeed>0</initialSpeed>
      <modelColor>0 0 0</modelColor>
      <modelName>Citroen_C4_Oriental_Blue</modelName>
      <motionId>-1</motionId>
      <name>Citroen_C4_Oriental_Blue</name>
      <process>${TRAFFIC}</process>
      <recomputeRestitutionMovement>1</recomputeRestitutionMovement>
      <state>0</state>
      <trailerId>-1</trailerId>
      <versionName></versionName>
      <ItineraryRoadXml>
        <endBehaviour>0</endBehaviour>
      </ItineraryRoadXml>
      <Model>
        <Simple/>
      </Model>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>2160.8115234375 5.24999999999489 0</position>
        <RoadPosition>
          <abscissa>1839.1884765625</abscissa>
          <gapInItnSystem>5.25</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
      <StopCriteria>
        <finalDistance>100000</finalDistance>
        <finalTime>3600</finalTime>
        <maxSideslipAngle>0.261799387799149</maxSideslipAngle>
        <maxSideslipAngleSpeed>0.349065850398866</maxSideslipAngleSpeed>
        <maxSpeed>111.111111111111</maxSpeed>
        <maxTrajectoryError>3</maxTrajectoryError>
        <minSpeed>0.277777777777778</minSpeed>
        <stopCanonContact>0</stopCanonContact>
        <stopConeCollision>0</stopConeCollision>
        <stopFinalDistance>0</stopFinalDistance>
        <stopFinalTime>0</stopFinalTime>
        <stopLateralAccelerationPeek>0</stopLateralAccelerationPeek>
        <stopMaxSpeed>0</stopMaxSpeed>
        <stopMinSpeed>0</stopMinSpeed>
        <stopSideslipAngle>0</stopSideslipAngle>
        <stopSideslipAngleSpeed>0</stopSideslipAngleSpeed>
        <stopSpin>0</stopSpin>
        <stopStableMovement>0</stopStableMovement>
        <stopStall>0</stopStall>
        <stopStallDuration>1</stopStallDuration>
        <stopStandstill>0</stopStandstill>
        <stopTrajectoryError>0</stopTrajectoryError>
        <stopWheelLift>0</stopWheelLift>
      </StopCriteria>
      <Swarm>
        <appearBackRadius>250</appearBackRadius>
        <appearFrontRadius>500</appearFrontRadius>
        <density>1</density>
        <disappearBackRadius>500</disappearBackRadius>
        <disappearFrontRadius>1000</disappearFrontRadius>
        <frontAppearFactor>0.75</frontAppearFactor>
        <queen>-1</queen>
        <queenDirectionFactor>0.3</queenDirectionFactor>
      </Swarm>
    </Vehicle>
    <Driver>
      <driverName>Driver 3</driverName>
      <driverType>TrafficDriver</driverType>
      <id>3</id>
      <process></process>
      <TrafficDriver>
        <accelCurveObserving>1</accelCurveObserving>
        <accelEmergencyPedestrianObserving>1</accelEmergencyPedestrianObserving>
        <accelEmergencyVehicleObserving>1</accelEmergencyVehicleObserving>
        <accelFollowObserving>1</accelFollowObserving>
        <accelRegulationObserving>1</accelRegulationObserving>
        <activateSharedDriving>0</activateSharedDriving>
        <autonomousToDriverTransitionTime>1</autonomousToDriverTransitionTime>
        <brakePedalThreashold>1</brakePedalThreashold>
        <disconectSteeringWhenAutonomous>1</disconectSteeringWhenAutonomous>
        <driverToAutonomousTransitionTime>5</driverToAutonomousTransitionTime>
        <gasPedalThreashold>0.01</gasPedalThreashold>
        <ignoreDriverPeriod>4</ignoreDriverPeriod>
        <laneBorderMargin>0.5</laneBorderMargin>
        <laneGapMean>0</laneGapMean>
        <laneGapPeriodMean>0</laneGapPeriodMean>
        <laneGapPeriodStdDev>0</laneGapPeriodStdDev>
        <laneGapStdDev>0.2</laneGapStdDev>
        <maxSpeed>0</maxSpeed>
        <norm>NormalDriver</norm>
        <overtakeRisk>0</overtakeRisk>
        <priorityObserving>1</priorityObserving>
        <safetyTime>2.5</safetyTime>
        <signObserving>1</signObserving>
        <speedLimitRisk>1</speedLimitRisk>
        <stayOnLane>1</stayOnLane>
        <steeringTorqueThreashold>5</steeringTorqueThreashold>
        <strategyFile>default</strategyFile>
      </TrafficDriver>
      <AirplaneDriver>
        <Trajectory>
          <isClosed>0</isClosed>
          <isWithObstacles>0</isWithObstacles>
          <notes></notes>
          <type>0</type>
          <CurvatureTrajectory>
            <initialHeading>0</initialHeading>
            <initialPosition>0 0</initialPosition>
          </CurvatureTrajectory>
          <XYTrajectory>
            <interpolationType>0</interpolationType>
          </XYTrajectory>
        </Trajectory>
      </AirplaneDriver>
    </Driver>
    <Trigger>
      <filter>*</filter>
      <height>2</height>
      <isActivated>1</isActivated>
      <isRectangle>0</isRectangle>
      <name>Trigger 2</name>
      <width>2</width>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>1934.89709472656 8.74999999999562 0</position>
        <RoadPosition>
          <abscissa>2065.10290527344</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
    </Trigger>
    <Trigger>
      <filter>*</filter>
      <height>2</height>
      <isActivated>1</isActivated>
      <isRectangle>0</isRectangle>
      <name>Trigger 3</name>
      <width>2</width>
      <ObjectPosition>
        <heading>3.14159274101257</heading>
        <position>1951.22631835938 8.74999999999556 0</position>
        <RoadPosition>
          <abscissa>2048.77368164062</abscissa>
          <gapInItnSystem>8.75</gapInItnSystem>
          <relativeHeading>0</relativeHeading>
          <subNetwork>Sub Network</subNetwork>
          <track>Track</track>
          <zOffset>0</zOffset>
        </RoadPosition>
      </ObjectPosition>
    </Trigger>
  </Scenario>

</sce>
