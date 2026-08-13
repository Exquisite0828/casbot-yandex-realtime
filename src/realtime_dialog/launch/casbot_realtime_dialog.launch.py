"""Configurable CASBOT compatibility launch profile."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    namespace = LaunchConfiguration("namespace")
    node_name = LaunchConfiguration("node_name")
    parameter_file = LaunchConfiguration("parameter_file")
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "namespace",
                default_value="lzdl10823",
                description="Device namespace; override for each robot",
            ),
            DeclareLaunchArgument(
                "node_name",
                default_value="dialog_node",
                description="Compatibility node name",
            ),
            DeclareLaunchArgument(
                "parameter_file",
                default_value=PathJoinSubstitution(
                    [
                        FindPackageShare("realtime_dialog"),
                        "config",
                        "casbot.example.yaml",
                    ]
                ),
                description="Phase 5 robot adapter parameters",
            ),
            Node(
                package="realtime_dialog",
                executable="realtime_dialog_node",
                namespace=namespace,
                name=node_name,
                parameters=[parameter_file],
                output="screen",
            ),
        ]
    )
