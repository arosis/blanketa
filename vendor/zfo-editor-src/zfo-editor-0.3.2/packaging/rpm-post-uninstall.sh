if [ $1 -eq 0 ] ; then
    update-mime-database %{_datadir}/mime &> /dev/null || :
	update-desktop-database &> /dev/null || :
fi
