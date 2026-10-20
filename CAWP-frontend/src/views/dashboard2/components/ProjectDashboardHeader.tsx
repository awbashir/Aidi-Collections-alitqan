type ProjectDashboardHeaderProps = {
    userName?: string
    taskCount?: number
}

const ProjectDashboardHeader = ({
    userName,
    taskCount,
}: ProjectDashboardHeaderProps) => {
    return (
        <div>
            <h4 className="text-[20px] font-extrabold tracking-[-0.01em] text-navy-600 dark:text-night-strong">
                {userName ? <>مرحباً، {userName}</> : 'مرحباً'}
            </h4>
            {/* <p>لديك  مهام قيد التنفيذ.</p> */}
        </div>
    )
}

export default ProjectDashboardHeader
